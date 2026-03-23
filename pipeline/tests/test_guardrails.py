"""Architectural guardrail tests — enforce coding principles via code inspection.

These tests inspect source files using AST and string scanning to enforce
rules that were previously documentation-only. They catch violations at
test time rather than relying on code review.
"""

import ast
import os

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = settings.BASE_DIR


def _python_files_in(directory):
    """Yield absolute paths of .py files in a directory (non-recursive for __init__)."""
    dir_path = os.path.join(BASE_DIR, directory)
    for filename in os.listdir(dir_path):
        if filename.endswith('.py') and filename != '__init__.py':
            yield os.path.join(dir_path, filename), filename


def _read_source(filepath):
    """Read and return file contents."""
    with open(filepath, 'r') as f:
        return f.read()


def _get_imports(source):
    """Return set of imported module paths from source code."""
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _get_attribute_calls(source):
    """Return set of method names called as obj.method() in source."""
    tree = ast.parse(source)
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    return calls


# ---------------------------------------------------------------------------
# 4.1 Conformance purity tests
# ---------------------------------------------------------------------------

class ConformancePurityTest(SimpleTestCase):
    """Conformance functions must be pure — no DB, no API calls."""

    def test_conformance_modules_have_no_db_imports(self):
        """No conformance file should import django.db or ORM modules."""
        db_modules = {'django.db', 'django.db.models'}
        for filepath, filename in _python_files_in('pipeline/conformance'):
            source = _read_source(filepath)
            imports = _get_imports(source)
            violations = imports & db_modules
            self.assertFalse(
                violations,
                f"pipeline/conformance/{filename} imports DB modules: "
                f"{violations} — conformance functions must be pure",
            )

    def test_conformance_modules_have_no_orm_calls(self):
        """No conformance file should call .objects., .save(), .create()."""
        orm_methods = {'save', 'create', 'bulk_create', 'update_or_create',
                       'get_or_create', 'delete', 'update'}
        for filepath, filename in _python_files_in('pipeline/conformance'):
            source = _read_source(filepath)
            calls = _get_attribute_calls(source)
            violations = calls & orm_methods
            # 'objects' check via string — AST-based call detection is sufficient
            self.assertFalse(
                violations,
                f"pipeline/conformance/{filename} calls ORM methods: "
                f"{violations} — conformance functions must be pure",
            )

    def test_conformance_modules_have_no_api_imports(self):
        """No conformance file should import connector or HTTP modules."""
        api_modules = {'httpx', 'requests', 'urllib', 'urllib3',
                       'pipeline.connectors'}
        for filepath, filename in _python_files_in('pipeline/conformance'):
            source = _read_source(filepath)
            imports = _get_imports(source)
            # Check for prefix matches (e.g. pipeline.connectors.geckoterminal)
            for imp in imports:
                for api_mod in api_modules:
                    self.assertFalse(
                        imp == api_mod or imp.startswith(api_mod + '.'),
                        f"pipeline/conformance/{filename} imports API module: "
                        f"{imp} — conformance functions must be pure",
                    )


# ---------------------------------------------------------------------------
# 4.2 Connector anti-corruption tests
# ---------------------------------------------------------------------------

class ConnectorAntiCorruptionTest(SimpleTestCase):
    """Connectors must not write to DB — they are anti-corruption layers."""

    def test_connectors_never_write_to_db(self):
        """No connector should call DB write methods."""
        write_methods = {'save', 'create', 'bulk_create', 'update_or_create',
                         'get_or_create', 'delete', 'update'}
        for filepath, filename in _python_files_in('pipeline/connectors'):
            source = _read_source(filepath)
            calls = _get_attribute_calls(source)
            violations = calls & write_methods
            self.assertFalse(
                violations,
                f"pipeline/connectors/{filename} calls DB write methods: "
                f"{violations} — connectors must not write to DB",
            )


# ---------------------------------------------------------------------------
# 4.3 Paradigm leak detection tests
# ---------------------------------------------------------------------------

DATASET_TERMS = {
    'mint_address', 'MigratedCoin', 'pump.fun', 'pumpswap', 'Pumpswap',
}

# Layer/reference IDs are OK as string constants in registries,
# but not in paradigm-level logic
DATASET_IDS = {'FL-001', 'FL-002', 'RD-001', 'U-001'}


class ParadigmLeakTest(SimpleTestCase):
    """Paradigm-level code must not contain dataset-specific terms."""

    def _extract_abstract_base_source(self):
        """Extract source code of abstract base classes from warehouse/models.py."""
        filepath = os.path.join(BASE_DIR, 'warehouse', 'models.py')
        source = _read_source(filepath)
        tree = ast.parse(source)
        abstract_sources = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Check for Meta.abstract = True
                for item in node.body:
                    if isinstance(item, ast.ClassDef) and item.name == 'Meta':
                        for meta_item in item.body:
                            if (isinstance(meta_item, ast.Assign)
                                    and any(t.id == 'abstract' for t in meta_item.targets
                                            if isinstance(t, ast.Name))
                                    and isinstance(meta_item.value, ast.Constant)
                                    and meta_item.value.value is True):
                                # Get the source lines for this class
                                abstract_sources.append(
                                    ast.get_source_segment(source, node) or ''
                                )
        return abstract_sources

    def test_no_paradigm_leaks_in_abstract_bases(self):
        """Abstract bases in warehouse/models.py must not reference dataset-specific terms."""
        for class_source in self._extract_abstract_base_source():
            for term in DATASET_TERMS:
                self.assertNotIn(
                    term, class_source,
                    f"Abstract base contains dataset-specific term '{term}' — "
                    f"paradigm leak",
                )

    def test_no_paradigm_leaks_in_managers(self):
        """warehouse/managers.py must not reference dataset-specific terms."""
        filepath = os.path.join(BASE_DIR, 'warehouse', 'managers.py')
        source = _read_source(filepath)
        for term in DATASET_TERMS:
            self.assertNotIn(
                term, source,
                f"warehouse/managers.py contains dataset-specific term '{term}' — "
                f"paradigm leak",
            )

    def test_no_paradigm_leaks_in_pipeline_runner(self):
        """pipeline/runner.py must not reference dataset-specific terms in its logic.

        The top-level imports of U-001 models for defaults are acceptable.
        The actual function bodies must not hardcode dataset-specific terms.
        """
        filepath = os.path.join(BASE_DIR, 'pipeline', 'runner.py')
        source = _read_source(filepath)
        tree = ast.parse(source)
        # Check function bodies (not imports) for hardcoded U-001 references
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_source = ast.get_source_segment(source, node) or ''
                # MigratedCoin/U001* OK in _resolve_models defaults,
                # but not in other functions
                if node.name != '_resolve_models':
                    for term in ('MigratedCoin', 'U001PipelineRun', 'U001PipelineStatus'):
                        self.assertNotIn(
                            term, func_source,
                            f"pipeline/runner.py function '{node.name}' "
                            f"hardcodes '{term}' — use spec models instead",
                        )


# ---------------------------------------------------------------------------
# 4.5 SSOT constant tests
# ---------------------------------------------------------------------------

class SSOTConstantTest(SimpleTestCase):
    """Pipeline code must not hardcode paradigm constants."""

    def _scan_pipeline_source(self, pattern, description, exclude_contexts=None):
        """Scan all non-test .py files in pipeline/ for a string pattern.

        Args:
            exclude_contexts: list of strings — if a line contains any of
                these strings, it's not a violation (false positive filter).
        """
        exclude_contexts = exclude_contexts or []
        violations = []
        for root, dirs, files in os.walk(os.path.join(BASE_DIR, 'pipeline')):
            # Skip test directories
            if 'tests' in root.split(os.sep):
                continue
            for filename in files:
                if not filename.endswith('.py') or filename == '__init__.py':
                    continue
                filepath = os.path.join(root, filename)
                source = _read_source(filepath)
                for i, line in enumerate(source.split('\n'), 1):
                    if pattern not in line:
                        continue
                    # Skip comments
                    if '#' in line.split(pattern)[0]:
                        continue
                    # Skip known false positives
                    if any(ctx in line for ctx in exclude_contexts):
                        continue
                    violations.append(f"{filepath}:{i}")
        return violations

    def test_no_hardcoded_temporal_resolution(self):
        """pipeline/ must not hardcode timedelta(minutes=5) — use model constant.

        Excludes overlap= parameters (operational, not paradigm constants).
        """
        violations = self._scan_pipeline_source(
            'timedelta(minutes=5)', 'TEMPORAL_RESOLUTION',
            exclude_contexts=['overlap'],
        )
        self.assertFalse(
            violations,
            f"Found hardcoded timedelta(minutes=5) in pipeline code "
            f"(should use model.TEMPORAL_RESOLUTION): {violations}",
        )

    def test_no_hardcoded_observation_window(self):
        """pipeline/ must not hardcode timedelta(minutes=5000) — use model constant."""
        violations = self._scan_pipeline_source(
            'timedelta(minutes=5000)', 'OBSERVATION_WINDOW_END',
        )
        self.assertFalse(
            violations,
            f"Found hardcoded timedelta(minutes=5000) in pipeline code "
            f"(should use model.OBSERVATION_WINDOW_END): {violations}",
        )

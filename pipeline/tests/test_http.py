from unittest.mock import Mock, patch

import httpx
from django.test import SimpleTestCase

from pipeline.connectors import http


class HttpConnectorTest(SimpleTestCase):
    def tearDown(self):
        http.shutdown_sessions()

    def test_http2_disabled_for_shyft_hosts_by_default(self):
        self.assertFalse(
            http._http2_enabled_for_url('https://api.shyft.to/sol/v1/transaction/parse_selected')
        )
        self.assertFalse(
            http._http2_enabled_for_url('https://rpc.shyft.to?api_key=test')
        )
        self.assertTrue(
            http._http2_enabled_for_url('https://api.geckoterminal.com/api/v2/networks/solana')
        )

    @patch.dict('os.environ', {'MARJON_HTTP2_DISABLED_HOSTS': 'example.com'}, clear=False)
    def test_http2_can_be_disabled_via_env(self):
        self.assertFalse(http._http2_enabled_for_url('https://example.com/data'))
        self.assertTrue(http._http2_enabled_for_url('https://another.example/data'))

    def test_shyft_client_kwargs_keep_http1_and_default_pooling(self):
        kwargs = http._client_kwargs_for_url(
            'https://api.shyft.to/sol/v1/transaction/parse_selected'
        )

        self.assertFalse(kwargs['http2'])
        self.assertNotIn('limits', kwargs)

    def test_non_shyft_client_kwargs_keep_default_pooling(self):
        kwargs = http._client_kwargs_for_url(
            'https://api.geckoterminal.com/api/v2/networks/solana'
        )

        self.assertTrue(kwargs['http2'])
        self.assertNotIn('limits', kwargs)

    def test_helius_keeps_http2_enabled_and_default_pooling(self):
        kwargs = http._client_kwargs_for_url(
            'https://api-mainnet.helius-rpc.com/v0/transactions'
        )

        self.assertTrue(kwargs['http2'])
        self.assertNotIn('limits', kwargs)

    def test_request_with_retry_retries_417_without_resetting_shyft_session(self):
        first_response = Mock()
        first_response.status_code = 417

        second_response = Mock()
        second_response.status_code = 200
        second_response.json.return_value = {'ok': True}

        session = Mock()
        session.get.side_effect = [first_response, second_response]

        with patch.object(http, '_get_session', return_value=session), \
                patch.object(http, '_drop_session') as drop_session, \
                patch('pipeline.connectors.http.time.sleep') as sleep:
            data = http.request_with_retry('https://api.shyft.to/sol/v1/transaction/history')

        self.assertEqual(data, {'ok': True})
        drop_session.assert_not_called()
        sleep.assert_called_once()

    def test_request_with_retry_retries_417_with_fresh_session_for_non_shyft(self):
        first_response = Mock()
        first_response.status_code = 417

        second_response = Mock()
        second_response.status_code = 200
        second_response.json.return_value = {'ok': True}

        session = Mock()
        session.get.side_effect = [first_response, second_response]

        with patch.object(http, '_get_session', return_value=session), \
                patch.object(http, '_drop_session') as drop_session, \
                patch('pipeline.connectors.http.time.sleep') as sleep:
            data = http.request_with_retry('https://api.geckoterminal.com/api/v2/networks/solana')

        self.assertEqual(data, {'ok': True})
        drop_session.assert_called_once_with(
            'https://api.geckoterminal.com/api/v2/networks/solana'
        )
        sleep.assert_called_once()

    def test_request_with_retry_keeps_shyft_session_on_transport_error(self):
        session = Mock()
        session.get.side_effect = [
            httpx.TransportError('boom'),
            Mock(status_code=200, json=Mock(return_value={'ok': True})),
        ]

        with patch.object(http, '_get_session', return_value=session), \
                patch.object(http, '_drop_session') as drop_session, \
                patch('pipeline.connectors.http.time.sleep'):
            data = http.request_with_retry('https://rpc.shyft.to?api_key=test')

        self.assertEqual(data, {'ok': True})
        drop_session.assert_not_called()

    def test_request_with_retry_keeps_helius_session_on_transport_error(self):
        session = Mock()
        session.post.side_effect = [
            httpx.TransportError('boom'),
            Mock(status_code=200, json=Mock(return_value=[])),
        ]

        with patch.object(http, '_get_session', return_value=session), \
                patch.object(http, '_drop_session') as drop_session, \
                patch('pipeline.connectors.http.time.sleep'):
            data = http.request_with_retry(
                'https://api-mainnet.helius-rpc.com/v0/transactions',
                method='POST',
                json_body={'transactions': ['sig']},
            )

        self.assertEqual(data, [])
        drop_session.assert_not_called()

    def test_request_with_retry_drops_non_shyft_session_on_transport_error(self):
        session = Mock()
        session.get.side_effect = [
            httpx.TransportError('boom'),
            Mock(status_code=200, json=Mock(return_value={'ok': True})),
        ]

        with patch.object(http, '_get_session', return_value=session), \
                patch.object(http, '_drop_session') as drop_session, \
                patch('pipeline.connectors.http.time.sleep'):
            data = http.request_with_retry('https://api.geckoterminal.com/api/v2/networks/solana')

        self.assertEqual(data, {'ok': True})
        drop_session.assert_called_once_with('https://api.geckoterminal.com/api/v2/networks/solana')

    def test_request_with_retry_raises_last_transport_error_detail(self):
        session = Mock()
        session.get.side_effect = httpx.RemoteProtocolError(
            'Server disconnected without sending a response.',
        )

        with patch.object(http, '_get_session', return_value=session), \
                patch.object(http, '_drop_session') as drop_session, \
                patch('pipeline.connectors.http.time.sleep'):
            with self.assertRaises(RuntimeError) as ctx:
                http.request_with_retry('https://rpc.shyft.to?api_key=test')

        self.assertIn('transport_error', str(ctx.exception))
        self.assertIn('Server disconnected without sending a response.', str(ctx.exception))
        drop_session.assert_not_called()

from django.test import TestCase
from django.urls import reverse


class VisualizationViewsTest(TestCase):
    def test_home_page_renders(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not just candles. Actual trade flow.')
        self.assertContains(response, 'Understand RD-001')

    def test_chart_view_still_renders(self):
        response = self.client.get(reverse('chart', args=['BTCUSDT']))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'BTCUSDT')

"""
Landing page and public-facing entry point views
"""
from django.shortcuts import render
from django.views.generic import TemplateView


class LandingPageView(TemplateView):
    """Public landing page with Bradsol branding and product selection."""
    template_name = 'landing.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Bradsol Contact Pvt Solution - Enterprise Finance & Procurement'
        return context

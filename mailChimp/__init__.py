"""
Mailchimp integration module for tagging event attendees and syncing audience.
"""

from .mailchimp_client import batch_tag_attendees, sanitize_event_name, sync_full_audience

__all__ = ['batch_tag_attendees', 'sanitize_event_name', 'sync_full_audience']

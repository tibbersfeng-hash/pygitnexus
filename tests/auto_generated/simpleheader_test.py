"""Auto-generated Playwright tests for SimpleHeader"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/components/SimpleHeader.vue
# Operations: 1

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/simpleheader'

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_goBack(page_setup: Page):
    """goBack (@click) → navigate to another page"""

    # Step 1: Trigger navigation
    page_setup.click("a")

    # Step 2: Verify navigation target
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 1
#   op_01_goBack: goBack (@click) → navigate to another page

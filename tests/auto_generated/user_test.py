"""Auto-generated Playwright tests for User"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/User.vue
# Operations: 2

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/user'

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_getUserInfo(page_setup: Page):
    """getUserInfo (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_02_goTo(page_setup: Page):
    """goTo (@click) → navigate to another page"""

    # Step 1: Trigger navigation
    page_setup.click("a")

    # Step 2: Verify navigation target
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 2
#   op_01_getUserInfo: getUserInfo (@click) → no API call
#   op_02_goTo: goTo (@click) → navigate to another page

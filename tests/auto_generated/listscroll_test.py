"""Auto-generated Playwright tests for ListScroll"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/components/ListScroll.vue
# Operations: 1

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/listscroll'

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_BScroll(page_setup: Page):
    """BScroll (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 1
#   op_01_BScroll: BScroll (@click) → no API call

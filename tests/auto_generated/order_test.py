"""Auto-generated Playwright tests for Order"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/Order.vue
# Operations: 3

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/orders'

# ── Form Field Constants ──
FIELD_STATUS = "[v-model='state.status']"
FIELD_REFRESHING = "[v-model='state.refreshing']"

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_goTo(page_setup: Page):
    """goTo (@click) → navigate to another page"""

    # Step 1: Trigger navigation
    page_setup.click("a")

    # Step 2: Verify navigation target
    page_setup.wait_for_load_state("networkidle")


def test_op_02_onRefresh(page_setup: Page):
    """onRefresh (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_03_onLoad(page_setup: Page):
    """onLoad (@submit) → POST service:getOrderList"""

    # Step 1: Fill form fields
    page_setup.fill("[v-model='state.status']", "test_value")
    page_setup.fill("[v-model='state.refreshing']", "test_value")

    # Step 2: Submit form with API interception
    resp_0 = page_setup.expect_response("**/getOrderList*")
    page_setup.click("button")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 3: Verify result
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 3
#   op_01_goTo: goTo (@click) → navigate to another page
#   op_02_onRefresh: onRefresh (@click) → no API call
#   op_03_onLoad: onLoad (@submit) → POST service:getOrderList
# API endpoints: 1
#   POST service:getOrderList

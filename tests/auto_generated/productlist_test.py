"""Auto-generated Playwright tests for ProductList"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/ProductList.vue
# Operations: 5

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/products'

# ── Form Field Constants ──
FIELD_KEYWORD = "[v-model='state.keyword']"
FIELD_REFRESHING = "[v-model='state.refreshing']"

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_goBack(page_setup: Page):
    """goBack (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("a")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_02_getSearch(page_setup: Page):
    """getSearch (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_03_productDetail(page_setup: Page):
    """productDetail (@click) → navigate to another page"""

    # Step 1: Trigger navigation
    page_setup.click("button")

    # Step 2: Verify navigation target
    page_setup.wait_for_load_state("networkidle")


def test_op_04_onRefresh(page_setup: Page):
    """onRefresh (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_05_onLoad(page_setup: Page):
    """onLoad (@submit) → POST service:search"""

    # Step 1: Fill form fields
    page_setup.fill("[v-model='state.keyword']", "test_value")
    page_setup.fill("[v-model='state.refreshing']", "test_value")
    page_setup.fill("input", "test_value")

    # Step 2: Submit form with API interception
    resp_0 = page_setup.expect_response("**/search*")
    page_setup.click("button")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 3: Verify result
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 5
#   op_01_goBack: goBack (@click) → no API call
#   op_02_getSearch: getSearch (@click) → no API call
#   op_03_productDetail: productDetail (@click) → navigate to another page
#   op_04_onRefresh: onRefresh (@click) → no API call
#   op_05_onLoad: onLoad (@submit) → POST service:search
# API endpoints: 1
#   POST service:search

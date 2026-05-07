"""Auto-generated Playwright tests for Setting"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/Setting.vue
# Operations: 3

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/settings'

# ── Form Field Constants ──
FIELD_NICK_NAME = "[v-model='state.nickName']"
FIELD_INTRODUCE_SIGN = "[v-model='state.introduceSign']"
FIELD_PASSWORD = "[v-model='state.password']"

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


def test_op_02_save(page_setup: Page):
    """save (@submit) → POST service:EditUserInfo"""

    # Step 1: Fill form fields
    page_setup.fill("[v-model='state.nickName']", "test_user")
    page_setup.fill("[v-model='state.introduceSign']", "test_value")
    page_setup.fill("[v-model='state.password']", "test_password123")

    # Step 2: Submit form with API interception
    resp_0 = page_setup.expect_response("**/EditUserInfo*")
    page_setup.click("form/button[type=submit]")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 3: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_03_handleLogout(page_setup: Page):
    """handleLogout (@submit) → POST service:logout"""

    # Step 1: Fill form fields
    page_setup.fill("[v-model='state.nickName']", "test_user")
    page_setup.fill("[v-model='state.introduceSign']", "test_value")
    page_setup.fill("[v-model='state.password']", "test_password123")

    # Step 2: Submit form with API interception
    resp_0 = page_setup.expect_response("**/logout*")
    page_setup.click("a")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 3: Verify result
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 3
#   op_01_getUserInfo: getUserInfo (@click) → no API call
#   op_02_save: save (@submit) → POST service:EditUserInfo
#   op_03_handleLogout: handleLogout (@submit) → POST service:logout
# API endpoints: 2
#   POST service:EditUserInfo
#   POST service:logout

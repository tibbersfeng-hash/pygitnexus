"""Auto-generated Playwright tests for Login"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/Login.vue
# Operations: 2

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/login'

# ── Form Field Constants ──
FIELD_USERNAME = "[v-model='state.username']"
FIELD_PASSWORD = "[v-model='state.password']"
FIELD_VERIFY = "[v-model='state.verify']"
FIELD_USERNAME1 = "[v-model='state.username1']"
FIELD_PASSWORD1 = "[v-model='state.password1']"
FIELD_VERIFY = "[v-model='state.verify']"

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_onSubmit(page_setup: Page):
    """onSubmit (@submit) → POST service:login, POST service:register"""

    # Step 1: Fill form fields
    page_setup.fill("[v-model='state.username']", "test_user")
    page_setup.fill("[v-model='state.password']", "test_password123")
    page_setup.fill("[v-model='state.verify']", "test_value")
    page_setup.fill("[v-model='state.password1']", "test_password123")
    page_setup.fill("[v-model='state.verify']", "test_value")

    # Step 2: Submit form with API interception
    resp_0 = page_setup.expect_response("**/login*")
    resp_1 = page_setup.expect_response("**/register*")
    page_setup.click("form/button[type=submit]")
    response = resp_0.value
    expect(response).to_be_ok()
    response = resp_1.value
    expect(response).to_be_ok()

    # Step 3: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_02_toggle(page_setup: Page):
    """toggle (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("a")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 2
#   op_01_onSubmit: onSubmit (@submit) → POST service:login, POST service:register
#   op_02_toggle: toggle (@click) → no API call
# API endpoints: 2
#   POST service:login
#   POST service:register

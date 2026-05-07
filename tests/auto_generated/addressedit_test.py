"""Auto-generated Playwright tests for AddressEdit"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/AddressEdit.vue
# Operations: 4

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/address-edit'

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_getAddressDetail(page_setup: Page):
    """getAddressDetail (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_02_back(page_setup: Page):
    """back (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("a")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_03_onSave(page_setup: Page):
    """onSave (@submit) → POST service:addAddress, POST service:EditAddress"""

    # Step 1: Submit form with API interception
    resp_0 = page_setup.expect_response("**/addAddress*")
    resp_1 = page_setup.expect_response("**/EditAddress*")
    page_setup.click("form/button[type=submit]")
    response = resp_0.value
    expect(response).to_be_ok()
    response = resp_1.value
    expect(response).to_be_ok()

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_04_onDelete(page_setup: Page):
    """onDelete (@submit) → POST service:DeleteAddress"""

    # Step 1: Submit form with API interception
    resp_0 = page_setup.expect_response("**/DeleteAddress*")
    page_setup.click("button.danger")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 4
#   op_01_getAddressDetail: getAddressDetail (@click) → no API call
#   op_02_back: back (@click) → no API call
#   op_03_onSave: onSave (@submit) → POST service:addAddress, POST service:EditAddress
#   op_04_onDelete: onDelete (@submit) → POST service:DeleteAddress
# API endpoints: 3
#   POST service:addAddress
#   POST service:EditAddress
#   POST service:DeleteAddress

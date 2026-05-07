"""Auto-generated Playwright tests for CreateOrder"""
# Source: /tmp/test-projects/newbee-mall-vue3-app/src/views/CreateOrder.vue
# Operations: 6

import re
import pytest
from playwright.sync_api import Page, expect

BASE_URL = 'http://localhost:5173'
PAGE_ROUTE = '/create-order'

@pytest.fixture
def page_setup(page: Page):
    """Fixture: navigate to page and wait for load."""
    page.goto(f"{BASE_URL}{PAGE_ROUTE}")
    page.wait_for_load_state("networkidle")
    return page

def test_op_01_init(page_setup: Page):
    """init (@submit) → POST service:getByCartItemIds, POST service:getAddressDetail, POST service:getDefaultAddress"""

    # Step 1: Submit form with API interception
    resp_0 = page_setup.expect_response("**/getByCartItemIds*")
    resp_1 = page_setup.expect_response("**/getAddressDetail*")
    resp_2 = page_setup.expect_response("**/getDefaultAddress*")
    page_setup.click("button")
    response = resp_0.value
    expect(response).to_be_ok()
    response = resp_1.value
    expect(response).to_be_ok()
    response = resp_2.value
    expect(response).to_be_ok()

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_02_goTo(page_setup: Page):
    """goTo (@click) → navigate to another page"""

    # Step 1: Trigger navigation
    page_setup.click("a")

    # Step 2: Verify navigation target
    page_setup.wait_for_load_state("networkidle")


def test_op_03_handleCreateOrder(page_setup: Page):
    """handleCreateOrder (@submit) → POST service:createOrder"""

    # Step 1: Submit form with API interception
    resp_0 = page_setup.expect_response("**/createOrder*")
    page_setup.click("button")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_04_handlePayOrder(page_setup: Page):
    """handlePayOrder (@submit) → POST service:payOrder"""

    # Step 1: Submit form with API interception
    resp_0 = page_setup.expect_response("**/payOrder*")
    page_setup.click("button")
    response = resp_0.value
    expect(response).to_be_ok()

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_05_deleteLocal(page_setup: Page):
    """deleteLocal (@click) → no API call"""

    # Step 1: Click the element
    page_setup.click("button.danger")

    # Step 2: Verify result
    page_setup.wait_for_load_state("networkidle")


def test_op_06_close(page_setup: Page):
    """close (@click) → navigate to another page"""

    # Step 1: Trigger navigation
    page_setup.click("button")

    # Step 2: Verify navigation target
    page_setup.wait_for_load_state("networkidle")


# ── Summary ──
# Total operations: 6
#   op_01_init: init (@submit) → POST service:getByCartItemIds, POST service:getAddressDetail, POST service:getDefaultAddress
#   op_02_goTo: goTo (@click) → navigate to another page
#   op_03_handleCreateOrder: handleCreateOrder (@submit) → POST service:createOrder
#   op_04_handlePayOrder: handlePayOrder (@submit) → POST service:payOrder
#   op_05_deleteLocal: deleteLocal (@click) → no API call
#   op_06_close: close (@click) → navigate to another page
# API endpoints: 5
#   POST service:getByCartItemIds
#   POST service:getAddressDetail
#   POST service:getDefaultAddress
#   POST service:createOrder
#   POST service:payOrder

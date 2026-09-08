# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Workflow flows for the Ad Seller System."""

from .deal_generation_flow import DealGenerationFlow
from .deal_request_flow import DealRequestFlow
from .discovery_inquiry_flow import DiscoveryInquiryFlow
from .execution_activation_flow import ExecutionActivationFlow
from .product_setup_flow import ProductSetupFlow
from .proposal_handling_flow import ProposalHandlingFlow

# Backward-compatibility alias
NonAgenticDSPFlow = DealRequestFlow

__all__ = [
    "ProductSetupFlow",
    "DiscoveryInquiryFlow",
    "ProposalHandlingFlow",
    "DealGenerationFlow",
    "DealRequestFlow",
    "NonAgenticDSPFlow",  # backward compat
    "ExecutionActivationFlow",
]

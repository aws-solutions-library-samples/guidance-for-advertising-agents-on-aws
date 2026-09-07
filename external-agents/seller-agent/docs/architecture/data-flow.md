# Data Flow

This page documents the primary transaction flows between buyer and seller agents. For buyer-side implementation details, see the [Buyer Agent docs](https://iabtechlab.github.io/buyer-agent/).

## Quote-to-Deal Flow

The most common transaction pattern. The buyer requests a quote, reviews the pricing, and books a deal.

```mermaid
sequenceDiagram
    participant Buyer as Buyer Agent
    participant API as Seller API
    participant Pricing as PricingEngine
    participant Storage as Storage

    Note over Buyer,Storage: 1. Quote Request
    Buyer->>API: POST /api/v1/quotes<br/>{product_id, deal_type, impressions}
    API->>Pricing: calculate_price(product, buyer_context, volume)
    Pricing-->>API: PricingDecision (final_cpm, discounts, rationale)
    API->>Storage: set_quote(quote_id, data, ttl=86400)
    API-->>Buyer: QuoteResponse {quote_id, pricing, terms, expires_at}

    Note over Buyer,Storage: 2. Quote Review (optional)
    Buyer->>API: GET /api/v1/quotes/{quote_id}
    API->>Storage: get_quote(quote_id)
    Storage-->>API: quote data
    API-->>Buyer: QuoteResponse

    Note over Buyer,Storage: 3. Deal Booking
    Buyer->>API: POST /api/v1/deals {quote_id}
    API->>Storage: get_quote(quote_id)
    Storage-->>API: quote data (status=available)
    API->>API: Generate Deal ID, OpenRTB params
    API->>Storage: set_quote(quote_id, status=booked)
    API->>Storage: set_deal(deal_id, deal_data)
    API-->>Buyer: DealBookingResponse {deal_id, openrtb_params, activation_instructions}

    Note over Buyer,Storage: 4. Order Creation
    Buyer->>API: POST /api/v1/orders {deal_id, quote_id}
    API->>Storage: set_order(order_id, state_machine)
    API-->>Buyer: Order {order_id, status=draft}
```

## Proposal-to-Deal Flow

For buyers who want to submit a custom proposal (different terms than a standard quote). Supports counter-offers and human approval gates.

```mermaid
sequenceDiagram
    participant Buyer as Buyer Agent
    participant API as Seller API
    participant Flow as ProposalHandlingFlow
    participant Approval as ApprovalGate
    participant Operator as Human Operator
    participant Storage as Storage

    Note over Buyer,Storage: 1. Proposal Submission
    Buyer->>API: POST /proposals<br/>{product_id, deal_type, price, impressions, dates}
    API->>Flow: handle_proposal(proposal_data, buyer_context, products)
    Flow->>Flow: Evaluate against pricing rules

    alt Auto-Accept (within acceptable range)
        Flow-->>API: {recommendation: "accept", status: "accepted"}
        API-->>Buyer: ProposalResponse {status: "accepted"}
    else Counter-Offer
        Flow-->>API: {recommendation: "counter", counter_terms: {...}}
        API-->>Buyer: ProposalResponse {status: "counter_offered", counter_terms}
    else Requires Human Approval
        Flow-->>API: {pending_approval: true}
        API->>Approval: request_approval(flow_state_snapshot)
        Approval->>Storage: Store approval request
        API-->>Buyer: ProposalResponse {status: "pending_approval", approval_id}
    end

    Note over Buyer,Storage: 2. Human Approval (if needed)
    Operator->>API: POST /approvals/{approval_id}/decide<br/>{decision: "approve"}
    API->>Approval: submit_decision(approval_id, decision)
    Approval->>Storage: Store decision

    Operator->>API: POST /approvals/{approval_id}/resume
    API->>Flow: Re-hydrate state, apply decision
    API-->>Operator: Final result

    Note over Buyer,Storage: 3. Deal Generation
    Buyer->>API: POST /deals {proposal_id}
    API->>Flow: generate_deal(proposal_id)
    Flow-->>API: {deal_id, openrtb_params}
    API-->>Buyer: DealResponse {deal_id, deal_type, price, openrtb_params}
```

## Negotiation Flow

When buyer and seller cannot agree on initial terms, they engage in multi-round negotiation. See [Negotiation Protocol](../integration/negotiation.md) for full details.

```mermaid
sequenceDiagram
    participant Buyer as Buyer Agent
    participant API as Seller API
    participant Engine as NegotiationEngine
    participant Storage as Storage

    Buyer->>API: POST /proposals {product_id, price: 8.00}
    API-->>Buyer: ProposalResponse {status: "counter_offered", counter_terms}

    loop Up to max_rounds
        Buyer->>API: POST /proposals/{id}/counter {buyer_price: 9.00}
        API->>Engine: evaluate_buyer_offer(history, buyer_price)
        Engine-->>API: NegotiationRound {action, seller_price, rationale}
        API->>Storage: set_negotiation(proposal_id, history)
        API-->>Buyer: {action: "counter", seller_price: 10.50, rounds_remaining: 2}
    end

    Note over Buyer,API: Final round: accept or walk away
    Buyer->>API: POST /proposals/{id}/counter {buyer_price: 10.00}
    API->>Engine: evaluate_buyer_offer(history, 10.00)
    Engine-->>API: {action: "accept", seller_price: 10.00}
    API-->>Buyer: {action: "accept", status: "accepted"}
```

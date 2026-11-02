# RideSharer Media Brand Safety and Suitability Guidelines

Version 2026.1. Owner: RideSharer Media brand safety lead. Applies to: every managed-service campaign that RideSharer Media plans or buys on behalf of a brand.

RideSharer is a fictional rideshare and food-delivery app that runs a commerce media network called RideSharer Media. The brands, rules and numbers in this document are synthetic workshop data.

## 1. Scope

These guidelines apply to both parts of a managed-service buy.

- **On-site**: ad placements inside the RideSharer app and RideSharer Eats, sold through direct deals in RideSharer's Prebid bidder (seller `ridesharer.example`). On-site surfaces are the ride request screen, the airport ride full-screen placement, the trip receipt, the Eats home feed and the Eats order tracking screen.
- **Off-site**: placements on other publishers across the open web, bought through RideSharer Media's buying seats (the AdCP and AAMP buyer agents). Off-site buys are made with RideSharer Media as the operator, so an unsafe off-site placement is attributed to RideSharer Media as well as to the brand.

Every rule has an id that starts with `RS-BS`. When the ad ops team or the AdOpsAgent rejects or changes part of a plan because of these guidelines, it names the rule id that applies.

## 2. Suitability tiers

Each managed-service brand has one suitability tier, recorded in its brand profile in section 5.

- **Conservative**: no hard news, no crime, no disasters, no conflict, no content about death or serious injury. Lifestyle, entertainment, sports and travel content only.
- **Moderate**: hard news is allowed except the exclusions in the brand's category rule and brand profile. This is the default tier for new brands.
- **Expanded**: all content above the brand safety floor (RS-BS-01), including news about crime and disasters. A brand can move to Expanded only with written approval from its own marketing lead.

## 3. Network-wide rules

### RS-BS-01 Brand safety floor

Rule RS-BS-01: no brand, in any tier, may run on-site or off-site next to content in these categories: adult and explicit sexual content, arms and ammunition, illegal drugs, hate speech and acts of aggression, terrorism, online piracy, spam and malware, and graphic death or injury. RS-BS-01 has no exceptions.

### RS-BS-02 Rider and courier safety surfaces carry no ads

Rule RS-BS-02: the RideSharer app shows no advertising on safety surfaces: the emergency assistance screen, the safety toolkit, the incident report flow, driver and courier identity verification, and any screen shown while a ride is flagged for a safety check. On-site deals must exclude these surfaces. RS-BS-02 has no exceptions.

### RS-BS-03 Sensitive locations are excluded from audiences

Rule RS-BS-03: no audience segment may be built from trips or deliveries that start or end at a sensitive location, and no campaign may target riders because they visited one. Sensitive locations are hospitals, clinics, pharmacies, mental health and addiction treatment centers, reproductive health facilities, places of worship, shelters, government benefits offices, immigration offices, courthouses, jails, and LGBTQ+ community centers. RS-BS-03 applies on-site and to every audience RideSharer Media shares with a buying seat for off-site use. RS-BS-03 has no exceptions, including for health, wellness or "recovery" offers.

Gyms, fitness studios, airports, hotels, offices, stadiums, restaurants and shopping centers are not sensitive locations, so audiences built from those trips are allowed when they meet RS-BS-04.

### RS-BS-04 Minimum audience size

Rule RS-BS-04: every audience built from RideSharer trip or order data must contain at least 1,000 riders or Eats customers. No segment may be built from a single pickup or drop-off address, and no campaign may retarget a rider within 24 hours of a specific trip.

### RS-BS-05 Alcohol

Rule RS-BS-05: alcohol brands may run only to age-verified accounts of riders 21 or older. Alcohol ads never run on rides requested between 22:00 and 05:00 local time from a nightlife venue (a bar, club or venue licensed for on-premise drinking), and never on the Eats order tracking screen of an order that contains no alcohol. Off-site alcohol buys must use an age-verified 21+ audience and exclude content aimed at audiences under 21.

### RS-BS-06 Gambling and cannabis

Rule RS-BS-06: gambling brands are not eligible for on-site placements. Off-site, gambling brands may run only in US states where the operator is licensed and only to a 21+ audience. Cannabis brands are not eligible on-site or off-site.

### RS-BS-07 Off-site verification

Rule RS-BS-07: every off-site line item must use pre-bid brand safety filtering for the brand's tier and post-bid verification from an independent measurement provider. If post-bid verification reports more than 0.5% brand-unsafe impressions on a line over any 24-hour period, the ad ops team pauses the line and reviews its exclusions before it resumes.

### RS-BS-08 News and breaking events

Rule RS-BS-08: off-site news placements follow the brand's tier. After a major tragedy (a mass-casualty event or a natural disaster with loss of life), RideSharer Media pauses all off-site news placements for every brand for 48 hours. Category rules can set a longer pause.

### RS-BS-09 RideSharer reputation

Rule RS-BS-09: because off-site buys carry RideSharer Media as the operator, no managed-service placement may run next to content about rideshare or food-delivery safety incidents, driver or courier misconduct, or traffic collisions involving rideshare vehicles. This applies to every brand and every tier.

## 4. Category rules

### RS-BS-10 Travel and hospitality

Rule RS-BS-10 applies to airlines, hotels, travel agencies, luggage and travel goods brands. Travel and hospitality brands exclude content about aviation accidents and incidents, travel advisories, terrorism, and severe weather or natural disasters at a destination. After a commercial aviation accident or serious incident anywhere in the world, all off-site news placements for travel and hospitality brands pause for 72 hours, which is longer than the 48-hour network pause in RS-BS-08. On-site, travel and hospitality brands are approved for the airport ride full-screen placement and the trip receipt.

### RS-BS-11 Restaurants and food delivery

Rule RS-BS-11 applies to restaurants, grocery and food-delivery brands. They exclude content about food safety incidents, product recalls, foodborne illness and eating disorders. On-site, they are approved for the Eats home feed and the Eats order tracking screen, but never for the order problem and refund screens.

### RS-BS-12 Automotive

Rule RS-BS-12 applies to car makers and dealers. Automotive brands exclude content about vehicle crashes, vehicle recalls and road fatalities. On-site, automotive brands are approved only for the trip receipt, not for screens shown during a ride.

## 5. Brand suitability profiles

| Brand | Category rule | Tier | Brand-specific exclusions | Approved on-site surfaces |
| --- | --- | --- | --- | --- |
| Altimara Travel | RS-BS-10 | Conservative | Airline strikes and airport disruption news | Airport ride full-screen, trip receipt |
| Ivory Row Luggage | RS-BS-10 | Moderate | None beyond RS-BS-10 | Airport ride full-screen |
| Veloria Stays | RS-BS-10 | Conservative | Hotel crime and hotel data-breach news | Airport ride full-screen, trip receipt |
| Basil & Brine Kitchen | RS-BS-11 | Moderate | Diet, weight-loss and calorie-counting content | Eats home feed, Eats order tracking |
| Maple Corner Noodle House | RS-BS-11 | Moderate | None beyond RS-BS-11 | Eats home feed, Eats order tracking |
| Northgate Motors | RS-BS-12 | Moderate | Competitor comparison reviews | Trip receipt |

### Altimara Travel

Altimara Travel is a Conservative-tier travel brand under RS-BS-10. Off-site, it runs on travel, lifestyle and entertainment content only, never on hard news, and it also excludes news about airline strikes and airport disruption. On-site, it runs on the airport ride full-screen placement and the trip receipt.

### Ivory Row Luggage

Ivory Row Luggage is a Moderate-tier travel goods brand under RS-BS-10. Off-site news is allowed except the RS-BS-10 exclusions and the 72-hour aviation pause. On-site, it runs on the airport ride full-screen placement.

### Veloria Stays

Veloria Stays is a Conservative-tier hotel brand under RS-BS-10. It also excludes news about crime at hotels and hotel data breaches. On-site, it runs on the airport ride full-screen placement and the trip receipt.

### Basil & Brine Kitchen

Basil & Brine Kitchen is a Moderate-tier restaurant brand under RS-BS-11. Its brand position is food people enjoy, so it never runs next to diet, weight-loss or calorie-counting content, and its creative makes no health claims. Healthy-eating recipe and cooking content is suitable; diet and weight-loss content is not. On-site, it runs on the Eats home feed and the Eats order tracking screen.

### Maple Corner Noodle House

Maple Corner Noodle House is a Moderate-tier restaurant brand under RS-BS-11 with no brand-specific exclusions. On-site, it runs on the Eats home feed and the Eats order tracking screen.

### Northgate Motors

Northgate Motors is a Moderate-tier automotive brand under RS-BS-12. It also excludes competitor comparison reviews. Off-site, connected TV drama and entertainment content is suitable. On-site, it runs on the trip receipt only.

## 6. How to check a plan

Check every managed-service plan in this order before it is proposed to the brand, and again before anything is booked.

1. Brand safety floor (RS-BS-01) and RideSharer reputation (RS-BS-09) for every placement.
2. On-site surfaces: no safety surfaces (RS-BS-02), and only the surfaces approved in the brand profile.
3. Audiences: no sensitive locations (RS-BS-03) and at least 1,000 people per segment (RS-BS-04).
4. Restricted categories: alcohol (RS-BS-05), gambling and cannabis (RS-BS-06).
5. The brand's category rule (RS-BS-10, RS-BS-11 or RS-BS-12) and its brand-specific exclusions.
6. Off-site verification (RS-BS-07) and any active news pause (RS-BS-08 or RS-BS-10).

When part of a plan conflicts with a rule, the ad ops team names the rule id, removes or replaces the conflicting part, and tells the brand what changed. It does not book the conflicting part while asking for an exception. Only the RideSharer Media brand safety lead can grant an exception, in writing, and RS-BS-01, RS-BS-02 and RS-BS-03 never have exceptions.

## 7. Incidents

When a brand-unsafe placement is found, the ad ops team pauses the affected line within 2 hours, notifies the brand within 24 hours with the placement, the rule id and the fix, and records the incident in the brand safety incident log.

## 8. Worked examples

**Can a travel brand target riders whose recent trips ended at a hospital with a recovery getaway offer?** No. RS-BS-03 excludes hospitals as sensitive locations, with no exceptions for health, wellness or recovery offers. Replace the audience with one that RS-BS-03 allows, such as riders with frequent airport trips.

**Can Altimara Travel extend its airport campaign off-site to news sites?** Not to hard news. Altimara Travel is Conservative tier, so off-site it runs on travel, lifestyle and entertainment content only (section 2 and its brand profile). Its off-site lines also need RS-BS-10 exclusions, the 72-hour aviation pause and RS-BS-07 verification.

**Can Ivory Row Luggage run on news sites the day after a commercial aviation accident?** No. RS-BS-10 pauses off-site news placements for travel and hospitality brands for 72 hours after a commercial aviation accident or serious incident.

**Can Basil & Brine Kitchen appear next to weight-loss content?** No. Its brand profile excludes diet, weight-loss and calorie-counting content. Healthy-eating recipe content is suitable.

**Can a fitness brand target riders whose trips end at gyms?** Yes. Gyms are not sensitive locations under RS-BS-03, so the audience is allowed when it has at least 1,000 riders (RS-BS-04).

**Can a hard seltzer brand run on late-night rides from bars?** No. RS-BS-05 blocks alcohol ads on rides requested between 22:00 and 05:00 from nightlife venues, and every alcohol ad needs an age-verified 21+ audience.

**Can a managed-service brand run next to a news story about a rideshare driver's collision?** No. RS-BS-09 excludes content about rideshare safety incidents and collisions for every brand.

**Can a sports betting brand buy the airport ride placement?** No. RS-BS-06 makes gambling brands ineligible on-site. Off-site, they may run only in licensed states to a 21+ audience.

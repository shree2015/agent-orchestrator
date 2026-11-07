# BUG-142: Analytics of a re-used alias include clicks from a deleted link

Reported by: Support (customer escalation). Severity: medium (wrong analytics data, no outage).

## What the customer saw
1. Created alias `launch-2026` for a campaign page and shared it. It collected a few clicks.
2. Deleted the link (the campaign ended), then later created `launch-2026` again for the next campaign.
3. `GET /api/links/launch-2026/stats` immediately reported the OLD clicks for the NEW link.

## Also observed
Support found click rows in some production databases whose link no longer exists.

## Constraints from the product owner
* No API changes: existing clients must keep working.
* Keep the change small and easy to review.
* Existing data must be repaired, not only future behaviour.

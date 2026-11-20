# Billing policy (implemented)

- Plans renew monthly through Paddle. Entitlements change **only** from verified webhooks or
  scheduled reconciliation with the Paddle API — never from the browser.
- **Upgrade:** effective when Paddle confirms. **Downgrade/cancel:** at the end of the period.
- **Failed payment:** subscription becomes `past_due`; a 14-day grace period (`DMX_BILLING_GRACE_DAYS`)
  keeps paid limits. Customer receives in-app + email notifications.
- **After grace / after cancellation:** effective limits fall back to Free. New resources beyond
  Free limits cannot be created. Databases above the Free limits get a notice and, after the grace
  period, are **stopped (not deleted)** — newest first — by `app/billing/enforcement.py`.
- **Deletion** of stopped resources is never automated; it requires an explicit support action
  after further notice.
- Refunds are processed in Paddle; approved refund adjustments mark the payment `refunded`.

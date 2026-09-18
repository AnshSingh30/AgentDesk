# Runbook: WhatsApp token expired / invalid

**Symptom**: deployment status `error`, last error "WhatsApp access token expired or invalid"; org admins get an in-app notification; `wa_send_failures_total{reason="token_invalid"}` increases.

1. In Meta Business Settings → System Users, generate a new permanent token with `whatsapp_business_messaging` and `whatsapp_business_management`.
2. In AgentDesk → agent → Deploy → "Pinned version, credentials & limits" paste the new token → Update credentials. Status moves to `paused`.
3. Click "Verify connection" (calls `GET /{phone_number_id}`) then Resume.
4. Inbound messages received while in error were acknowledged (200) but dropped; ask the customer to resend if needed.

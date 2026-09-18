# Connect WhatsApp (Cloud API)

1. Create a Meta developer app (type Business) and add the **WhatsApp** product.
2. Use the free **test number** (up to 5 verified recipients) or register your own number.
3. Create a **System User** in Meta Business Settings and generate a **permanent token** (temporary tokens expire in ~24 h).
4. Copy the **Phone Number ID**, **WhatsApp Business Account ID** and the app's **App Secret**.
5. In AgentDesk → your agent → Deploy, paste them and create the deployment. Copy the **Callback URL** and **Verify token** into Meta → WhatsApp → Configuration, subscribe to the `messages` field.
6. Click **Verify connection**, send a **test message** to an allowed recipient, then go live.

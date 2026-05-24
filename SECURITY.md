# Security

This repository is public-safe by design. Do not commit real secrets.

Keep these values only in local `.env` files or hosting provider environment variables:

- `GEMINI_API_KEY`
- `LINE_CHANNEL_ID`
- `LINE_CHANNEL_SECRET`
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- Any Google service account JSON file

If a real key is ever committed or pasted into a public issue, rotate it immediately in the provider console.

For Render deployment, set secrets in the Render Dashboard Environment page. Do not paste real values into `.env.example`, `README.md`, or `render.yaml`.

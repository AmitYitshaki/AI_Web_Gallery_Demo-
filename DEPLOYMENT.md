# ImageForge AI Deployment Guide

This project is ready to deploy with Docker. The recommended production path is:

1. Push the code to GitHub.
2. Deploy the GitHub repo to Render as a Docker web service.
3. Add a Render persistent disk mounted at `/app/static/uploads`.
4. Add all secrets as Render environment variables.

## Files That Must Stay Private

Do not commit these files:

- `.env`
- `firebase_config.json`
- `static/uploads/*`

They are ignored by `.gitignore` and `.dockerignore`.

## Local Docker Test

From the project root:

```bash
docker build -t imageforge-ai .
docker run --env-file .env -p 5000:5000 -v imageforge_uploads:/app/static/uploads imageforge-ai
```

Then open:

```text
http://127.0.0.1:5000
```

## Render Setup

Create a new Render Web Service:

- Source: your GitHub repo
- Runtime: Docker
- Instance type: choose a free or paid plan

Add a persistent disk:

- Mount path: `/app/static/uploads`
- Size: choose the smallest size that fits your demo needs

## Render Environment Variables

Add these in Render's Environment tab:

```env
FLASK_SECRET_KEY=
COOKIE_SECURE=true
FLASK_DEBUG=false

FIREBASE_API_KEY=
FIREBASE_AUTH_DOMAIN=
FIREBASE_PROJECT_ID=
FIREBASE_MESSAGING_SENDER_ID=
FIREBASE_APP_ID=
FIREBASE_SERVICE_ACCOUNT_JSON=

HUGGINGFACE_API_KEY=
HUGGINGFACE_PROVIDER=fal-ai
HUGGINGFACE_MODEL=black-forest-labs/FLUX.1-Kontext-dev
```

For `FIREBASE_SERVICE_ACCOUNT_JSON`, paste the full Firebase service account JSON content as one environment variable value. Do not commit `firebase_config.json`.

## Firebase Auth Authorized Domain

After Render deploys, copy your Render domain, for example:

```text
imageforge-ai.onrender.com
```

In Firebase Console:

1. Authentication
2. Settings
3. Authorized domains
4. Add the Render domain without `https://` and without a trailing slash

## Firestore Rules

Use rules that restrict each user to their own document tree:

```js
rules_version = '2';

service cloud.firestore {
  match /databases/{database}/documents {
    match /users/{userId}/{document=**} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }
  }
}
```

## Production Notes

- Uploaded files are served from `/static/uploads`.
- The Render disk is required so uploads survive redeploys/restarts.
- Hugging Face credits can be exhausted. The app includes a local demo fallback for depleted credits.
- For a public production app, consider moving physical image storage to a managed object store later.

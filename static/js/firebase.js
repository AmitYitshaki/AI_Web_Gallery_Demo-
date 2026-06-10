import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.4/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  signOut,
} from "https://www.gstatic.com/firebasejs/10.12.4/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js";

const REQUIRED_FIREBASE_CONFIG_KEYS = [
  "apiKey",
  "authDomain",
  "projectId",
  "messagingSenderId",
  "appId",
];

async function loadFirebaseConfig() {
  const response = await fetch("/api/firebase-config", {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("Could not load Firebase client configuration.");
  }

  const config = await response.json();
  const missingFields = REQUIRED_FIREBASE_CONFIG_KEYS.filter((key) => !config[key]);

  if (missingFields.length > 0) {
    throw new Error(`Missing Firebase config values: ${missingFields.join(", ")}`);
  }

  return config;
}

async function initializeFirebaseServices() {
  const config = await loadFirebaseConfig();
  const app = initializeApp(config);

  return {
    app,
    auth: getAuth(app),
    db: getFirestore(app),
    googleProvider: new GoogleAuthProvider(),
  };
}

export const initFirebasePromise = initializeFirebaseServices();

export async function getFirebaseServices() {
  return initFirebasePromise;
}

export async function getCurrentUser() {
  const { auth } = await initFirebasePromise;

  return new Promise((resolve) => {
    const unsubscribe = onAuthStateChanged(auth, (user) => {
      unsubscribe();
      resolve(user);
    });
  });
}

export async function requireUser() {
  const user = await getCurrentUser();

  if (!user) {
    window.location.href = "/";
    return null;
  }

  return user;
}

export async function getIdToken() {
  const user = await requireUser();
  return user ? user.getIdToken() : null;
}

export async function createBackendSession(user) {
  const idToken = await user.getIdToken();
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idToken }),
  });

  if (!response.ok) {
    let message = "Could not create backend session.";

    try {
      const data = await response.json();
      if (data.error) {
        message = data.error;
      }
    } catch (_error) {
      // Keep the generic message if the server did not return JSON.
    }

    throw new Error(message);
  }

  return response.json();
}

export async function logout() {
  const { auth } = await initFirebasePromise;
  await signOut(auth);
  await fetch("/api/session", { method: "DELETE" });
  window.location.href = "/";
}

export function wireLogoutButtons() {
  document.querySelectorAll("[data-logout]").forEach((button) => {
    button.addEventListener("click", logout);
  });
}

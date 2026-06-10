import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signInWithPopup,
} from "https://www.gstatic.com/firebasejs/10.12.4/firebase-auth.js";
import {
  doc,
  getDoc,
  serverTimestamp,
  setDoc,
} from "https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js";

import { createBackendSession, getFirebaseServices } from "./firebase.js";

const authPanel = document.querySelector("#authPanel");
const authTitle = document.querySelector("#authTitle");
const authForm = document.querySelector("#authForm");
const emailInput = document.querySelector("#email");
const passwordInput = document.querySelector("#password");
const authMessage = document.querySelector("#authMessage");
const howToModal = document.querySelector("#howToModal");
const closeHowToModal = document.querySelector("#closeHowToModal");

let authMode = "login";

document.querySelector("#loginButton").addEventListener("click", () => openAuth("login"));
document.querySelector("#registerButton").addEventListener("click", () => openAuth("register"));
document.querySelector("#howToButton").addEventListener("click", openHowToModal);
closeHowToModal.addEventListener("click", closeHowToModalOverlay);
howToModal.addEventListener("click", (event) => {
  if (event.target === howToModal) {
    closeHowToModalOverlay();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && howToModal.classList.contains("is-visible")) {
    closeHowToModalOverlay();
  }
});
document.querySelector("#googleButton").addEventListener("click", handleGoogleLogin);

authForm.addEventListener("submit", handleEmailAuth);

function openAuth(mode) {
  authMode = mode;
  authPanel.classList.add("is-visible");
  authTitle.textContent = mode === "login" ? "Login" : "Create Account";
  authForm.querySelector("button[type='submit']").textContent =
    mode === "login" ? "Login" : "Register";
  authMessage.textContent = "";
  emailInput.focus();
}

function openHowToModal() {
  howToModal.classList.add("is-visible");
  closeHowToModal.focus();
}

function closeHowToModalOverlay() {
  howToModal.classList.remove("is-visible");
  document.querySelector("#howToButton").focus();
}

async function handleEmailAuth(event) {
  event.preventDefault();
  setAuthMessage("Checking credentials...");

  const email = emailInput.value.trim();
  const password = passwordInput.value;

  try {
    const { auth, db } = await getFirebaseServices();
    const credential =
      authMode === "login"
        ? await signInWithEmailAndPassword(auth, email, password)
        : await createUserWithEmailAndPassword(auth, email, password);

    await ensureUserDocument(db, credential.user);
    await createBackendSession(credential.user);
    window.location.href = "/dashboard";
  } catch (error) {
    setAuthMessage(readableFirebaseError(error));
  }
}

async function handleGoogleLogin() {
  setAuthMessage("Opening Google...");

  try {
    const { auth, db, googleProvider } = await getFirebaseServices();
    const credential = await signInWithPopup(auth, googleProvider);

    await ensureUserDocument(db, credential.user);
    await createBackendSession(credential.user);
    window.location.href = "/dashboard";
  } catch (error) {
    setAuthMessage(readableFirebaseError(error));
  }
}

async function ensureUserDocument(db, user) {
  const userRef = doc(db, "users", user.uid);
  const snapshot = await getDoc(userRef);

  if (snapshot.exists()) {
    await setDoc(userRef, { email: user.email || "" }, { merge: true });
    return;
  }

  await setDoc(userRef, {
    email: user.email || "",
    createdAt: serverTimestamp(),
  });
}

function setAuthMessage(message) {
  authMessage.textContent = message;
}

function readableFirebaseError(error) {
  if (!error.code) {
    return error.message || "Something went wrong.";
  }

  return error.code
    .replace("auth/", "")
    .replaceAll("-", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

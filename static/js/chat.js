import {
  addDoc,
  collection,
  doc,
  serverTimestamp,
  setDoc,
} from "https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js";

import { getFirebaseServices, getIdToken, requireUser, wireLogoutButtons } from "./firebase.js";

const fileInput = document.querySelector("#imageUpload");
const attachButton = document.querySelector("#attachButton");
const sendButton = document.querySelector("#sendButton");
const promptInput = document.querySelector("#promptInput");
const chatHistory = document.querySelector("#chatHistory");
const selectedImage = document.querySelector("#selectedImage");
const selectedImageName = document.querySelector("#selectedImageName");

let currentUser;
let currentOriginal = null;
let isBusy = false;

wireLogoutButtons();
initializeStudio();
updateSendState();

attachButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", handleFileSelection);
sendButton.addEventListener("click", handlePromptSubmit);
promptInput.addEventListener("input", updateSendState);
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    handlePromptSubmit();
  }
});

async function initializeStudio() {
  currentUser = await requireUser();
  if (!currentUser) {
    return;
  }

  addSystemMessage("Upload an image, then describe the variation you want.");
}

async function handleFileSelection(event) {
  const file = event.target.files?.[0];
  if (!file || !currentUser) {
    return;
  }

  setComposerEnabled(false);
  selectedImageName.textContent = "Uploading image...";

  try {
    const { db } = await getFirebaseServices();
    const idToken = await getIdToken();
    const formData = new FormData();
    formData.append("image", file);

    const uploadResponse = await fetch("/api/upload", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${idToken}`,
      },
      body: formData,
    });
    const uploadData = await uploadResponse.json();

    if (!uploadResponse.ok) {
      throw new Error(uploadData.error || "Image upload failed.");
    }

    const originalDoc = doc(collection(db, "users", currentUser.uid, "original_images"));
    await setDoc(originalDoc, {
      url: uploadData.imageUrl,
      localPath: uploadData.imageUrl,
      uploadedAt: serverTimestamp(),
    });

    currentOriginal = {
      id: originalDoc.id,
      url: uploadData.imageUrl,
      localPath: uploadData.imageUrl,
      name: file.name,
    };

    selectedImage.src = uploadData.imageUrl;
    selectedImage.hidden = false;
    selectedImageName.textContent = file.name;
    addImageMessage("user", uploadData.imageUrl, "Uploaded original image");
    showToast("Image uploaded successfully.", "success");
  } catch (error) {
    const message = error.message || "Image upload failed.";
    addErrorMessage(message);
    showToast(message, "error");
    selectedImageName.textContent = "No image selected";
  } finally {
    setComposerEnabled(true);
    fileInput.value = "";
  }
}

async function handlePromptSubmit() {
  const prompt = promptInput.value.trim();

  if (!currentOriginal) {
    const message = "Choose an image before sending a prompt.";
    addErrorMessage(message);
    showToast(message, "error");
    return;
  }

  if (!prompt) {
    const message = "Type a prompt before sending.";
    addErrorMessage(message);
    showToast(message, "error");
    return;
  }

  promptInput.value = "";
  setComposerEnabled(false);
  addTextMessage("user", prompt);
  const loadingMessage = addLoadingMessage();

  try {
    const idToken = await getIdToken();
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
      },
      body: JSON.stringify({
        imageUrl: currentOriginal.url,
        prompt,
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Generation failed.");
    }

    loadingMessage.remove();
    addGeneratedMessage(data.imageUrl, prompt, data.isFallback, data.message);
    showToast(data.isFallback ? "Demo variation generated." : "AI variation generated.", "success");
  } catch (error) {
    loadingMessage.remove();
    const message = `Error: ${error.message || "AI generation failed. Please try again."}`;
    addErrorMessage(message);
    showToast(message, "error");
  } finally {
    setComposerEnabled(true);
  }
}

function addGeneratedMessage(imageUrl, prompt, isFallback = false, message = "") {
  const bubble = createMessage("ai");
  bubble.innerHTML = `
    <p>${isFallback ? "Here is a demo fallback variation." : "Here is your generated variation."}</p>
    ${isFallback ? `<p class="message-note">${escapeHtml(message || "Hosted AI credits are depleted.")}</p>` : ""}
    <img class="message-image" src="${escapeAttribute(imageUrl)}" alt="Generated variation">
    <div class="message-actions">
      <button class="btn btn-primary" type="button">Save to Gallery</button>
      <button class="btn btn-secondary" type="button">Discard</button>
    </div>
  `;

  const [saveButton, discardButton] = bubble.querySelectorAll("button");
  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    saveButton.textContent = "Saving...";

    try {
      await saveVariation(imageUrl, prompt);
      saveButton.textContent = "Saved";
      discardButton.remove();
      showToast("Variation saved to gallery.", "success");
    } catch (error) {
      saveButton.disabled = false;
      saveButton.textContent = "Save to Gallery";
      const message = error.message || "Could not save variation.";
      addErrorMessage(message);
      showToast(message, "error");
    }
  });

  discardButton.addEventListener("click", () => {
    bubble.classList.add("message--removing");
    window.setTimeout(() => {
      bubble.remove();
      addSystemMessage("Image discarded.");
      showToast("Image discarded.", "success");
    }, 220);
  });

  appendMessage(bubble);
}

async function saveVariation(imageUrl, prompt) {
  const { db } = await getFirebaseServices();
  const variationsRef = collection(
    db,
    "users",
    currentUser.uid,
    "original_images",
    currentOriginal.id,
    "processed_variations"
  );

  await addDoc(variationsRef, {
    prompt,
    url: imageUrl,
    createdAt: serverTimestamp(),
  });
}

function addTextMessage(sender, text) {
  const bubble = createMessage(sender);
  bubble.textContent = text;
  appendMessage(bubble);
}

function addImageMessage(sender, imageUrl, caption) {
  const bubble = createMessage(sender);
  bubble.innerHTML = `
    <p>${escapeHtml(caption)}</p>
    <img class="message-image" src="${escapeAttribute(imageUrl)}" alt="${escapeAttribute(caption)}">
  `;
  appendMessage(bubble);
}

function addSystemMessage(text) {
  const bubble = createMessage("system");
  bubble.textContent = text;
  appendMessage(bubble);
}

function addErrorMessage(text) {
  const bubble = createMessage("error");
  bubble.textContent = text;
  appendMessage(bubble);
}

function addLoadingMessage() {
  const bubble = createMessage("ai");
  bubble.innerHTML = `
    <span class="spinner" aria-hidden="true"></span>
    <span>AI is generating...</span>
  `;
  appendMessage(bubble);
  return bubble;
}

function createMessage(sender) {
  const bubble = document.createElement("article");
  bubble.className = `message message--${sender}`;
  return bubble;
}

function appendMessage(element) {
  chatHistory.appendChild(element);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

function setComposerEnabled(enabled) {
  isBusy = !enabled;
  promptInput.disabled = isBusy;
  attachButton.disabled = isBusy;
  updateSendState();
}

function updateSendState() {
  const hasPrompt = promptInput.value.trim().length > 0;
  sendButton.disabled = isBusy || !currentOriginal || !hasPrompt;
}

function showToast(message, type = "success") {
  let toastStack = document.querySelector("#toastStack");

  if (!toastStack) {
    toastStack = document.createElement("div");
    toastStack.id = "toastStack";
    toastStack.className = "toast-stack";
    toastStack.setAttribute("aria-live", "polite");
    document.body.appendChild(toastStack);
  }

  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.textContent = message;
  toastStack.appendChild(toast);

  window.setTimeout(() => {
    toast.classList.add("toast--leaving");
    window.setTimeout(() => toast.remove(), 180);
  }, 4200);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value || "");
}

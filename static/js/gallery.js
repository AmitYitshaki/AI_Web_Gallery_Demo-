import {
  collection,
  getDocs,
  orderBy,
  query,
} from "https://www.gstatic.com/firebasejs/10.12.4/firebase-firestore.js";

import { getFirebaseServices, getIdToken, requireUser, wireLogoutButtons } from "./firebase.js";

const galleryGrid = document.querySelector("#galleryGrid");
const emptyState = document.querySelector("#emptyState");
const modal = document.querySelector("#variationModal");
const modalOriginal = document.querySelector("#modalOriginal");
const variationGrid = document.querySelector("#variationGrid");
const modalTitle = document.querySelector("#modalTitle");
const deleteModal = document.querySelector("#deleteModal");
const closeDeleteModalButton = document.querySelector("#closeDeleteModal");
const cancelDeleteButton = document.querySelector("#cancelDeleteButton");
const confirmDeleteButton = document.querySelector("#confirmDeleteButton");

let pendingDelete = null;

wireLogoutButtons();
document.querySelector("#closeModal").addEventListener("click", closeModal);
modal.addEventListener("click", (event) => {
  if (event.target === modal) {
    closeModal();
  }
});
closeDeleteModalButton.addEventListener("click", closeDeleteModal);
cancelDeleteButton.addEventListener("click", closeDeleteModal);
confirmDeleteButton.addEventListener("click", confirmDelete);
deleteModal.addEventListener("click", (event) => {
  if (event.target === deleteModal) {
    closeDeleteModal();
  }
});

loadGallery();

async function loadGallery() {
  const user = await requireUser();
  if (!user) {
    return;
  }

  const { db } = await getFirebaseServices();
  const originalsRef = collection(db, "users", user.uid, "original_images");
  const originalsQuery = query(originalsRef, orderBy("uploadedAt", "desc"));
  const snapshot = await getDocs(originalsQuery);

  galleryGrid.innerHTML = "";
  emptyState.hidden = !snapshot.empty;

  snapshot.forEach((imageDoc) => {
    const image = { id: imageDoc.id, ...imageDoc.data() };
    galleryGrid.appendChild(renderImageCard(user.uid, image));
  });
}

function renderImageCard(uid, image) {
  const card = document.createElement("article");
  card.className = "image-card";
  card.innerHTML = `
    <button class="image-card__preview" type="button" aria-label="Open image variations">
      <img src="${escapeAttribute(image.url)}" alt="Original upload">
    </button>
    <span class="image-card__meta">${formatDate(image.uploadedAt)}</span>
    <button class="image-card__delete" type="button" aria-label="Delete image">Delete</button>
  `;

  card.querySelector(".image-card__preview").addEventListener("click", () => openVariations(uid, image));
  card.querySelector(".image-card__delete").addEventListener("click", () => openDeleteModal(card, image));
  return card;
}

function openDeleteModal(card, image) {
  pendingDelete = { card, image };
  deleteModal.classList.add("is-visible");
  confirmDeleteButton.disabled = false;
  confirmDeleteButton.textContent = "Delete";
  confirmDeleteButton.focus();
}

function closeDeleteModal() {
  deleteModal.classList.remove("is-visible");
  pendingDelete = null;
}

async function confirmDelete() {
  if (!pendingDelete) {
    return;
  }

  const { card, image } = pendingDelete;
  confirmDeleteButton.disabled = true;
  confirmDeleteButton.textContent = "Deleting...";

  try {
    const idToken = await getIdToken();
    const response = await fetch(`/api/images/${encodeURIComponent(image.id)}`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${idToken}`,
      },
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Could not delete image.");
    }

    card.classList.add("image-card--removing");
    window.setTimeout(() => {
      card.remove();
      emptyState.hidden = galleryGrid.children.length > 0;
    }, 180);
    closeDeleteModal();
    showToast("Image deleted.", "success");
  } catch (error) {
    confirmDeleteButton.disabled = false;
    confirmDeleteButton.textContent = "Delete";
    showToast(error.message || "Could not delete image.", "error");
  }
}

async function openVariations(uid, image) {
  const { db } = await getFirebaseServices();
  modalTitle.textContent = "Processed Variations";
  modalOriginal.src = image.url;
  variationGrid.innerHTML = '<p class="muted">Loading variations...</p>';
  modal.classList.add("is-visible");

  const variationsRef = collection(
    db,
    "users",
    uid,
    "original_images",
    image.id,
    "processed_variations"
  );
  const variationsQuery = query(variationsRef, orderBy("createdAt", "desc"));
  const snapshot = await getDocs(variationsQuery);

  if (snapshot.empty) {
    variationGrid.innerHTML = '<p class="muted">No saved variations yet.</p>';
    return;
  }

  variationGrid.innerHTML = "";
  snapshot.forEach((variationDoc) => {
    const variation = variationDoc.data();
    const item = document.createElement("article");
    item.className = "variation-card";
    item.innerHTML = `
      <img src="${escapeAttribute(variation.url)}" alt="Generated variation">
      <div>
        <p>${escapeHtml(variation.prompt || "Untitled prompt")}</p>
        <span>${formatDate(variation.createdAt)}</span>
      </div>
    `;
    variationGrid.appendChild(item);
  });
}

function closeModal() {
  modal.classList.remove("is-visible");
  modalOriginal.removeAttribute("src");
}

function formatDate(timestamp) {
  const date = timestamp?.toDate ? timestamp.toDate() : null;
  return date ? date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "New";
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
  }, 3200);
}

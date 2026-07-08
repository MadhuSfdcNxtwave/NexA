import { getUser } from "./auth.js";

const KEY = "nexa_favorites";

function storageKey() {
  const uid = getUser()?.id;
  return uid ? `${KEY}_${uid}` : KEY;
}

export function getFavorites() {
  try {
    const raw = localStorage.getItem(storageKey());
    const ids = JSON.parse(raw || "[]");
    return Array.isArray(ids) ? ids.map(Number).filter(Boolean) : [];
  } catch {
    return [];
  }
}

export function isFavorite(projectId) {
  return getFavorites().includes(Number(projectId));
}

export function toggleFavorite(projectId) {
  const id = Number(projectId);
  const set = new Set(getFavorites());
  if (set.has(id)) set.delete(id);
  else set.add(id);
  localStorage.setItem(storageKey(), JSON.stringify([...set]));
  window.dispatchEvent(new Event("nexa-favorites-updated"));
  return set.has(id);
}

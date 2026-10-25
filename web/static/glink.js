/* Branch Chat 0.23.1: group invite link crypto (Signal-style; see docs/INVITES.md).
   A link is  <origin>/join#j1.<secret>  where <secret> is 32 random bytes (base64url). Browsers never send the part
   after "#" to the server. From the secret we derive, with HKDF-SHA-256:
     proof   = HKDF(secret, "bc-glink-proof-v1")  -> sent to the server only when someone opens or redeems the link;
                                                     the server stores sha256(proof) and never the secret.
     cardKey = HKDF(secret, "bc-glink-card-v1")   -> opens the "card" (group id + name) so the join screen can show
                                                     the name without the server learning it.
   The link holds no message key: joining moves the group to a fresh epoch, so a link can't unlock history. */
import {encryptJson, decryptJson} from "./crypto.js?v=2";

const SALT = new TextEncoder().encode("branch-chat-glink-salt-v1");
const PREFIX = "j1.";

// Bytes to URL-safe base64 without padding (for links).
export function b64u(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i]);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// URL-safe base64 back to bytes, or null when it isn't valid.
export function fromB64u(text) {
  if (typeof text !== "string" || !/^[A-Za-z0-9_-]+$/.test(text)) return null;
  const pad = text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4);
  try {
    const raw = atob(pad);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
    return out;
  } catch (err) {
    return null;
  }
}

// Derive a 32-byte key from the link secret for one purpose (HKDF-SHA-256 with a fixed label).
async function hkdf(secret, info) {
  const base = await crypto.subtle.importKey("raw", secret, "HKDF", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({name: "HKDF", hash: "SHA-256", salt: SALT, info: new TextEncoder().encode(info)}, base, 256);
  return new Uint8Array(bits);
}

// SHA-256 of text as hex.
async function sha256Hex(text) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}

// A fresh random 32-byte secret for a new group invite link.
export function newSecret() {
  return crypto.getRandomValues(new Uint8Array(32));
}

// From a link secret: the proof sent to the server, the key that seals the group card, and the server's lookup hash.
export async function linkKeys(secret) {
  const proof = b64u(await hkdf(secret, "bc-glink-proof-v1"));
  const cardKey = await hkdf(secret, "bc-glink-card-v1");
  return {proof, cardKey, lookup: await sha256Hex(proof)};
}

// The invite link itself; the secret stays after # so it never reaches a server.
export function linkUrl(origin, secret) {
  return origin.replace(/\/+$/, "") + "/join#" + PREFIX + b64u(secret);
}

// Read the secret back out of a link's # part, or null.
export function secretFromHash(hash) {
  const text = String(hash || "").replace(/^#/, "");
  if (!text.startsWith(PREFIX)) return null;
  const bytes = fromB64u(text.slice(PREFIX.length));
  return bytes && bytes.length === 32 ? bytes : null;
}

// Seal the group card (group id and name) so only people with the link can read it.
export async function sealCard(cardKey, gid, name) {
  return encryptJson(cardKey, {v: 1, g: gid, name});
}

// Open a sealed group card with the key from the link; null when it doesn't open.
export async function openCard(cardKey, card) {
  if (!card || !card.iv || !card.ct) return null;
  try {
    const value = await decryptJson(cardKey, card.iv, card.ct);
    return value && typeof value.g === "string" ? value : null;
  } catch (err) {
    return null;
  }
}

// The secret sealed with the group's epoch key, so the admin's other devices (and other admins who hold that epoch)
// can show the same link again. The server can't open it.
export async function sealSecret(epochKey, secret) {
  return encryptJson(epochKey, {s: b64u(secret)});
}

// Members recover the link secret from its copy sealed with the group key (to show the link again).
export async function openSecret(epochKey, seal) {
  try {
    const value = await decryptJson(epochKey, seal.iv, seal.ct);
    const bytes = fromB64u(value && value.s);
    return bytes && bytes.length === 32 ? bytes : null;
  } catch (err) {
    return null;
  }
}

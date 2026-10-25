/* Browser encryption for Branch Chat.
   The server never receives a private key or a message key. */

const WRAP_SALT = new TextEncoder().encode("huzz-chat-salt-v1");
const WRAP_INFO = new TextEncoder().encode("huzz-chat-wrap-v1");

export function bytesToB64(bytes) {
  let text = "";
  const step = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += step) {
    text += String.fromCharCode(...bytes.subarray(offset, offset + step));
  }
  return btoa(text);
}

export function b64ToBytes(text) {
  const raw = atob(text);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

export function makePassphrase() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  let out = "";
  for (let i = 0; i < bytes.length; i += 1) {
    out += alphabet[bytes[i] % alphabet.length];
    if (i % 4 === 3 && i !== bytes.length - 1) out += "-";
  }
  return out;
}

export async function createIdentity() {
  const pair = await crypto.subtle.generateKey(
    {name: "ECDH", namedCurve: "P-256"},
    true,
    ["deriveBits"]
  );
  const deviceId = [...crypto.getRandomValues(new Uint8Array(16))]
    .map((part) => part.toString(16).padStart(2, "0"))
    .join("");
  return {deviceId, privateKey: pair.privateKey, publicKey: pair.publicKey};
}

export async function exportPublic(publicKey) {
  return bytesToB64(new Uint8Array(await crypto.subtle.exportKey("spki", publicKey)));
}

export async function importPublic(text) {
  return crypto.subtle.importKey(
    "spki",
    b64ToBytes(text),
    {name: "ECDH", namedCurve: "P-256"},
    true,
    []
  );
}

async function aesFromShared(secret, usage) {
  const hkdf = await crypto.subtle.importKey("raw", secret, "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    {name: "HKDF", hash: "SHA-256", salt: WRAP_SALT, info: WRAP_INFO},
    hkdf,
    {name: "AES-GCM", length: 256},
    false,
    [usage]
  );
}

export async function wrapFor(publicKey, keyBytes) {
  const eph = await crypto.subtle.generateKey(
    {name: "ECDH", namedCurve: "P-256"},
    true,
    ["deriveBits"]
  );
  const secret = await crypto.subtle.deriveBits(
    {name: "ECDH", public: publicKey},
    eph.privateKey,
    256
  );
  const aes = await aesFromShared(secret, "encrypt");
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({name: "AES-GCM", iv}, aes, keyBytes));
  const ephPub = new Uint8Array(await crypto.subtle.exportKey("spki", eph.publicKey));
  return {eph_pub: bytesToB64(ephPub), iv: bytesToB64(iv), ct: bytesToB64(ct)};
}

export async function unwrap(privateKey, wrap) {
  const ephPub = await importPublic(wrap.eph_pub);
  const secret = await crypto.subtle.deriveBits(
    {name: "ECDH", public: ephPub},
    privateKey,
    256
  );
  const aes = await aesFromShared(secret, "decrypt");
  const plain = await crypto.subtle.decrypt(
    {name: "AES-GCM", iv: b64ToBytes(wrap.iv)},
    aes,
    b64ToBytes(wrap.ct)
  );
  return new Uint8Array(plain);
}

async function importAes(keyBytes, usage) {
  return crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, [usage]);
}

export async function encryptBytes(keyBytes, plain) {
  const aes = await importAes(keyBytes, "encrypt");
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({name: "AES-GCM", iv}, aes, plain));
  return {iv: bytesToB64(iv), ct: bytesToB64(ct)};
}

export async function decryptBytes(keyBytes, ivText, ctText) {
  const aes = await importAes(keyBytes, "decrypt");
  const plain = await crypto.subtle.decrypt(
    {name: "AES-GCM", iv: b64ToBytes(ivText)},
    aes,
    b64ToBytes(ctText)
  );
  return new Uint8Array(plain);
}

export async function encryptJson(keyBytes, value) {
  return encryptBytes(keyBytes, new TextEncoder().encode(JSON.stringify(value)));
}

export async function decryptJson(keyBytes, ivText, ctText) {
  const plain = await decryptBytes(keyBytes, ivText, ctText);
  return JSON.parse(new TextDecoder().decode(plain));
}

export async function encryptChunk(aes, plain) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({name: "AES-GCM", iv}, aes, plain));
  const packed = new Uint8Array(12 + ct.length);
  packed.set(iv, 0);
  packed.set(ct, 12);
  return packed;
}

export async function decryptChunk(keyBytes, packed) {
  const aes = await importAes(keyBytes, "decrypt");
  const plain = await crypto.subtle.decrypt(
    {name: "AES-GCM", iv: packed.subarray(0, 12)},
    aes,
    packed.subarray(12)
  );
  return new Uint8Array(plain);
}

export async function importFileKey(keyBytes) {
  return importAes(keyBytes, "encrypt");
}

async function passKey(passphrase, salt, usage) {
  // 200000 matches the server backup seal. A different count cannot open those files.
  const base = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  return crypto.subtle.deriveKey(
    {name: "PBKDF2", salt, iterations: 200000, hash: "SHA-256"},
    base,
    {name: "AES-GCM", length: 256},
    false,
    [usage]
  );
}

export async function sealRecovery(deviceId, privateKey, publicKey, passphrase) {
  // Same seal for a recovery file and a password backup. The passphrase differs. The server stores only the backup.
  const inner = new TextEncoder().encode(JSON.stringify({
    device_id: deviceId,
    pkcs8: bytesToB64(new Uint8Array(await crypto.subtle.exportKey("pkcs8", privateKey))),
    spki: bytesToB64(new Uint8Array(await crypto.subtle.exportKey("spki", publicKey))),
  }));
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const aes = await passKey(passphrase, salt, "encrypt");
  const ct = new Uint8Array(await crypto.subtle.encrypt({name: "AES-GCM", iv}, aes, inner));
  return JSON.stringify({
    v: 1,
    salt: bytesToB64(salt),
    iv: bytesToB64(iv),
    ct: bytesToB64(ct),
  });
}

export async function openRecovery(jsonText, passphrase) {
  const file = JSON.parse(jsonText);
  if (!file || file.v !== 1) throw new Error("That recovery file was not recognized.");
  const aes = await passKey(passphrase, b64ToBytes(file.salt), "decrypt");
  let inner;
  try {
    inner = await crypto.subtle.decrypt(
      {name: "AES-GCM", iv: b64ToBytes(file.iv)},
      aes,
      b64ToBytes(file.ct)
    );
  } catch (err) {
    throw new Error("That code did not open the recovery file.");
  }
  const parsed = JSON.parse(new TextDecoder().decode(inner));
  const privateKey = await crypto.subtle.importKey(
    "pkcs8",
    b64ToBytes(parsed.pkcs8),
    {name: "ECDH", namedCurve: "P-256"},
    true,
    ["deriveBits"]
  );
  const publicKey = await importPublic(parsed.spki);
  return {deviceId: parsed.device_id, privateKey, publicKey};
}

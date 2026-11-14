export const BACKEND_URL = (process.env.EXPO_PUBLIC_BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const WS_URL = BACKEND_URL.replace(/^http/, "ws");

/** Turn a backend-relative path (/tts/x.mp3, /media/y.png) into an absolute URL. */
export const absUrl = (u: string) => (u.startsWith("http") ? u : `${BACKEND_URL}${u}`);

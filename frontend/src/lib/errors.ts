import { ApiError } from "../api/client";

/** What to show when a call fails: the server's own plain-language reason, or a calm general line. */
export const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");

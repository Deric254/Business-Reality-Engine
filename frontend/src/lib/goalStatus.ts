import type { GoalStatus } from "../api/client";

export const STATUS: Record<GoalStatus, { label: string; color: string; text: string }> = {
  met: { label: "Target reached", color: "#1f9254", text: "#146c3e" },
  on_track: { label: "On track", color: "#1f9254", text: "#146c3e" },
  at_risk: { label: "At risk", color: "#d4a017", text: "#8a5a00" },
  off_track: { label: "Off track", color: "#d92d20", text: "#b42318" },
  not_started: { label: "Just started", color: "#8c968f", text: "#4f5a54" },
  no_data: { label: "No data yet", color: "#8c968f", text: "#4f5a54" },
};

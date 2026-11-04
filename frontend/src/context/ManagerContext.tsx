import { createContext, useContext, useState, type ReactNode } from "react";
import { setRequestManagerId } from "../api/client";

export type ManagerId = "m-001" | "m-002";

interface ManagerContextValue {
  managerId: ManagerId;
  setManagerId: (id: ManagerId) => void;
}

const ManagerContext = createContext<ManagerContextValue | null>(null);

function initialManager(): ManagerId {
  try {
    return window.sessionStorage.getItem("shiftguard.managerId") === "m-002" ? "m-002" : "m-001";
  } catch {
    return "m-001";
  }
}

export function ManagerProvider({ children }: { children: ReactNode }) {
  const [managerId, updateManagerId] = useState<ManagerId>(initialManager);

  const setManagerId = (next: ManagerId) => {
    updateManagerId(next);
    setRequestManagerId(next);
  };

  return (
    <ManagerContext.Provider value={{ managerId, setManagerId }}>
      {children}
    </ManagerContext.Provider>
  );
}

export function useManager(): ManagerContextValue {
  const value = useContext(ManagerContext);
  if (!value) throw new Error("useManager must be used inside ManagerProvider");
  return value;
}

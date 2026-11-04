import { useManager, type ManagerId } from "../context/ManagerContext";

const MANAGERS: Record<ManagerId, string> = {
  "m-001": "Avery Singh",
  "m-002": "Jordan Blake",
};

export function ManagerSwitcher() {
  const { managerId, setManagerId } = useManager();
  return (
    <label className="manager-switcher">
      <span>Reviewing as</span>
      <select
        aria-label="Manager"
        value={managerId}
        onChange={(event) => setManagerId(event.target.value as ManagerId)}
      >
        {Object.entries(MANAGERS).map(([id, name]) => (
          <option key={id} value={id}>{name}</option>
        ))}
      </select>
    </label>
  );
}

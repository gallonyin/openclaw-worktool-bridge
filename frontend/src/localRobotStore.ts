import { LOCAL_ROBOTS_STORAGE_KEY } from './constants';

export interface LocalRobotItem {
  robot_id: string;
  name?: string;
}

function normalizeRobotId(value: string): string {
  return (value || '').trim();
}

export function loadLocalRobots(): LocalRobotItem[] {
  try {
    const raw = localStorage.getItem(LOCAL_ROBOTS_STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    const seen = new Set<string>();
    const result: LocalRobotItem[] = [];
    for (const item of arr) {
      const robotId = normalizeRobotId(item?.robot_id || item?.robotId || '');
      if (!robotId || seen.has(robotId)) continue;
      seen.add(robotId);
      const name = ((item?.name as string) || '').trim();
      result.push({ robot_id: robotId, name: name || undefined });
    }
    return result;
  } catch {
    return [];
  }
}

export function saveLocalRobots(items: LocalRobotItem[]): void {
  const seen = new Set<string>();
  const normalized: LocalRobotItem[] = [];
  for (const item of items || []) {
    const robotId = normalizeRobotId(item?.robot_id || '');
    if (!robotId || seen.has(robotId)) continue;
    seen.add(robotId);
    const name = (item?.name || '').trim();
    normalized.push({ robot_id: robotId, name: name || undefined });
  }
  try {
    localStorage.setItem(LOCAL_ROBOTS_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // ignore storage errors
  }
}

export function upsertLocalRobot(item: LocalRobotItem): LocalRobotItem[] {
  const robotId = normalizeRobotId(item?.robot_id || '');
  if (!robotId) return loadLocalRobots();
  const name = (item?.name || '').trim();
  const current = loadLocalRobots();
  const next = current.map((x) => (x.robot_id === robotId ? { ...x, name: name || x.name } : x));
  if (!next.some((x) => x.robot_id === robotId)) {
    next.push({ robot_id: robotId, name: name || undefined });
  }
  saveLocalRobots(next);
  return next;
}

export function removeLocalRobot(robotId: string): LocalRobotItem[] {
  const id = normalizeRobotId(robotId);
  const next = loadLocalRobots().filter((x) => x.robot_id !== id);
  saveLocalRobots(next);
  return next;
}

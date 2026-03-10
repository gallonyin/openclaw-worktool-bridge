const LAST_SELECTED_ROBOT_ID_KEY = 'selected_robot_id';

export function getLastSelectedRobotId(): string | undefined {
  try {
    const value = localStorage.getItem(LAST_SELECTED_ROBOT_ID_KEY);
    return value || undefined;
  } catch {
    return undefined;
  }
}

export function setLastSelectedRobotId(robotId?: string) {
  try {
    if (robotId) {
      localStorage.setItem(LAST_SELECTED_ROBOT_ID_KEY, robotId);
    } else {
      localStorage.removeItem(LAST_SELECTED_ROBOT_ID_KEY);
    }
  } catch {
    // ignore storage errors
  }
}

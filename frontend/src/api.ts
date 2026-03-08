import axios from 'axios';

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 15000
});

export const api = {
  health: () => http.get('/health').then((r) => r.data),
  getOverview: () => http.get('/dashboard/overview').then((r) => r.data),
  getTrends: (days = 7) => http.get('/dashboard/trends', { params: { days } }).then((r) => r.data),
  getWorktoolSettings: () => http.get('/settings/worktool').then((r) => r.data),
  getCallbackBaseSuggestions: () => http.get('/settings/callback-base-suggestions').then((r) => r.data),
  updateWorktoolSettings: (payload: {
    worktool_api_base?: string;
    callback_public_base_url?: string;
    auto_bind_message_callback_on_create?: boolean;
  }) =>
    http.put('/settings/worktool', payload).then((r) => r.data),
  listRobots: () => http.get('/robots').then((r) => r.data.items),
  createRobot: (payload: any) => http.post('/robots', payload).then((r) => r.data),
  updateRobot: (robotId: string, payload: any) => http.put(`/robots/${robotId}`, payload).then((r) => r.data),
  deleteRobot: (robotId: string) => http.delete(`/robots/${robotId}`).then((r) => r.data),
  listProviders: (robotId?: string) => http.get('/providers', { params: robotId ? { robot_id: robotId } : {} }).then((r) => r.data.items),
  createProvider: (payload: any) => http.post('/providers', payload).then((r) => r.data),
  updateProvider: (id: number, payload: any) => http.put(`/providers/${id}`, payload).then((r) => r.data),
  listRules: (robotId: string, scene?: string) => http.get(`/robots/${robotId}/rules`, { params: scene ? { scene } : {} }).then((r) => r.data.items),
  createRule: (payload: any) => http.post('/rules', payload).then((r) => r.data),
  updateRule: (id: number, payload: any) => http.put(`/rules/${id}`, payload).then((r) => r.data),
  reorderRules: (robotId: string, scene: 'group' | 'private', ruleIds: number[]) =>
    http.put(`/robots/${robotId}/rules/reorder`, { rule_ids: ruleIds }, { params: { scene } }).then((r) => r.data),
  listMessageLogs: (params: any) => http.get('/logs/messages', { params }).then((r) => r.data),
  getMessageLog: (id: number) => http.get(`/logs/messages/${id}`).then((r) => r.data),
  getWorktoolQaLogs: (params: any) => http.get('/worktool/qa-logs', { params }).then((r) => r.data),
  getRobotInfoDetail: (robotId: string) => http.get('/robot-info/detail', { params: { robot_id: robotId } }).then((r) => r.data),
  getRobotInfoCallbacks: (robotId: string) => http.get('/robot-info/callbacks', { params: { robot_id: robotId } }).then((r) => r.data),
  getRobotInfoOnline: (robotId: string) => http.get('/robot-info/online', { params: { robot_id: robotId } }).then((r) => r.data),
  getRobotInfoOnlineInfos: (robotId: string) => http.get('/robot-info/online-infos', { params: { robot_id: robotId } }).then((r) => r.data),
  testRobotMessageCallback: (robotId: string, callbackUrl: string) =>
    http.post('/robot-info/message-callback/test', { robot_id: robotId, callback_url: callbackUrl }).then((r) => r.data),
  testRobotCallback2xx: (callbackUrl: string) =>
    http.post('/robot-info/callbacks/test', { callback_url: callbackUrl }).then((r) => r.data),
  bindRobotMessageCallback: (robotId: string, callbackUrl: string) =>
    http.post('/robot-info/message-callback/bind', { robot_id: robotId, callback_url: callbackUrl }).then((r) => r.data),
  bindRobotCallbackType: (robotId: string, callbackUrl: string, type: number) =>
    http.post('/robot-info/callbacks/bind', { robot_id: robotId, callback_url: callbackUrl, type }).then((r) => r.data),
  deleteRobotCallbackByType: (robotId: string, type: number, robotKey = '') =>
    http.post('/robot-info/callbacks/delete-by-type', { robot_id: robotId, type, robot_key: robotKey }).then((r) => r.data),
  troubleshootSearch: (payload: any) => http.post('/troubleshoot/search', payload).then((r) => r.data)
};

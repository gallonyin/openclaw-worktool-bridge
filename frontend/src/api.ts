import axios from 'axios';
import { AUTH_TOKEN_STORAGE_KEY } from './constants';

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 15000
});

export function getAccessToken() {
  try {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

export function setAccessToken(token: string) {
  try {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  } catch {
    // ignore storage errors
  }
}

export function clearAccessToken() {
  try {
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  } catch {
    // ignore storage errors
  }
}

http.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers = config.headers || {};
    (config.headers as any).Authorization = `Bearer ${token}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    if (status === 401) {
      clearAccessToken();
      if (window.location.pathname !== '/login') {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/login?next=${next}`;
      }
    }
    return Promise.reject(error);
  }
);

export const api = {
  authConfig: () => http.get('/auth/config').then((r) => r.data),
  authLogin: (payload: { phone: string; password: string }) => http.post('/auth/login', payload).then((r) => r.data),
  authMe: () => http.get('/auth/me').then((r) => r.data),
  authLogoutAll: () => http.post('/auth/logout-all').then((r) => r.data),
  authSendSms: (payload: { phone: string; scene: 'register' | 'reset_password' | 'login' }) =>
    http.post('/auth/sms/send', payload).then((r) => r.data),
  authRegister: (payload: { phone: string; sms_code?: string; password: string; company_name?: string }) =>
    http.post('/auth/register', payload).then((r) => r.data),
  authResetPassword: (payload: { phone: string; sms_code: string; new_password: string }) =>
    http.post('/auth/password/reset', payload).then((r) => r.data),
  adminListUsers: (params: { phone?: string; page?: number; page_size?: number }) =>
    http.get('/admin/users', { params }).then((r) => r.data),
  health: () => http.get('/health').then((r) => r.data),
  getOverview: () => http.get('/dashboard/overview').then((r) => r.data),
  getTrends: (days = 7) => http.get('/dashboard/trends', { params: { days } }).then((r) => r.data),
  getWorktoolSettings: () => http.get('/settings/worktool').then((r) => r.data),
  updateWorktoolSettings: (payload: {
    worktool_api_base?: string;
    callback_public_base_url?: string;
    auto_bind_message_callback_on_create?: boolean;
  }) =>
    http.put('/settings/worktool', payload).then((r) => r.data),
  listRobots: () => http.get('/robots').then((r) => r.data.items),
  getRobot: (robotId: string) => http.get(`/robots/${robotId}`).then((r) => r.data),
  createRobot: (payload: any) => http.post('/robots', payload).then((r) => r.data),
  updateRobot: (robotId: string, payload: any) => http.put(`/robots/${robotId}`, payload).then((r) => r.data),
  deleteRobot: (robotId: string) => http.delete(`/robots/${robotId}`).then((r) => r.data),
  listProviders: (robotId?: string) => http.get('/providers', { params: robotId ? { robot_id: robotId } : {} }).then((r) => r.data.items),
  createProvider: (payload: any) => http.post('/providers', payload).then((r) => r.data),
  updateProvider: (id: number, payload: any) => http.put(`/providers/${id}`, payload).then((r) => r.data),
  deleteProvider: (id: number) => http.delete(`/providers/${id}`).then((r) => r.data),
  listRules: (robotId: string, scene?: string) => http.get(`/robots/${robotId}/rules`, { params: scene ? { scene } : {} }).then((r) => r.data.items),
  createRule: (payload: any) => http.post('/rules', payload).then((r) => r.data),
  updateRule: (id: number, payload: any) => http.put(`/rules/${id}`, payload).then((r) => r.data),
  deleteRule: (id: number) => http.delete(`/rules/${id}`).then((r) => r.data),
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

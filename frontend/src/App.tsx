import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Badge, Button, Layout, Menu, Space, Typography } from 'antd';
import {
  DashboardOutlined,
  RobotOutlined,
  FileTextOutlined,
  ApiOutlined,
  BellOutlined,
  BuildOutlined,
  ProfileOutlined,
  InfoCircleOutlined,
  NotificationOutlined,
  SearchOutlined,
  ShareAltOutlined,
  StopOutlined,
  TeamOutlined
} from '@ant-design/icons';
import DashboardPage from './pages/DashboardPage';
import RobotPage from './pages/RobotPage';
import MessageLogPage from './pages/MessageLogPage';
import AIHubPage from './pages/AIHubPage';
import ForwardPage from './pages/ForwardPage';
import RobotInfoPage from './pages/RobotInfoPage';
import TroubleshootPage from './pages/TroubleshootPage';
import CommandTaskPage from './pages/CommandTaskPage';
import InboxPage from './pages/InboxPage';
import InboxAdminPage from './pages/InboxAdminPage';
import LoginPage from './pages/LoginPage';
import UserManagementPage from './pages/UserManagementPage';
import IpBlacklistPage from './pages/IpBlacklistPage';
import EnterpriseAuthorizationPage from './pages/EnterpriseAuthorizationPage';
import { api, clearAccessToken, getAccessToken } from './api';

const { Header, Sider, Content } = Layout;

function maskPhone(phone?: string) {
  const p = String(phone || '').trim();
  if (/^1\d{10}$/.test(p)) {
    return `${p.slice(0, 3)}****${p.slice(7)}`;
  }
  return p || '-';
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [enableTroubleshoot, setEnableTroubleshoot] = useState(false);
  const [enableAdminIpBlacklist, setEnableAdminIpBlacklist] = useState(false);
  const [enableAdminEnterpriseAuth, setEnableAdminEnterpriseAuth] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [userPhone, setUserPhone] = useState('');
  const [robotInitChecked, setRobotInitChecked] = useState(false);
  const [inboxUnreadCount, setInboxUnreadCount] = useState(0);

  useEffect(() => {
    if (location.pathname === '/login') {
      setAuthed(false);
      setIsAdmin(false);
      setUserPhone('');
      setRobotInitChecked(false);
      setAuthReady(true);
      return;
    }
    const token = getAccessToken();
    if (!token) {
      setAuthed(false);
      setIsAdmin(false);
      setUserPhone('');
      setRobotInitChecked(false);
      setAuthReady(true);
      return;
    }
    setAuthReady(false);
    let mounted = true;
    api
      .authMe()
      .then((me) => {
        if (mounted) {
          setAuthed(true);
          setIsAdmin(Boolean(me?.is_admin));
          setUserPhone(String(me?.phone || ''));
          setAuthReady(true);
        }
      })
      .catch(() => {
        if (mounted) {
          clearAccessToken();
          setAuthed(false);
          setIsAdmin(false);
          setUserPhone('');
          setAuthReady(true);
        }
      });
    return () => {
      mounted = false;
    };
  }, [location.pathname]);

  useEffect(() => {
    if (!authed) return;
    let mounted = true;
    api
      .health()
      .then((d) => {
        if (mounted) {
          setEnableTroubleshoot(Boolean(d?.enable_troubleshoot));
          setEnableAdminIpBlacklist(Boolean(d?.enable_admin_ip_blacklist));
          setEnableAdminEnterpriseAuth(Boolean(d?.enable_admin_enterprise_auth));
        }
      })
      .catch(() => {
        if (mounted) {
          setEnableTroubleshoot(false);
          setEnableAdminIpBlacklist(false);
          setEnableAdminEnterpriseAuth(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, [authed]);

  useEffect(() => {
    if (!authed) return;
    let canceled = false;
    const loadUnread = async () => {
      try {
        const res = await api.inboxUnreadCount();
        if (!canceled) {
          setInboxUnreadCount(Number(res?.count || 0));
        }
      } catch {
        if (!canceled) {
          setInboxUnreadCount(0);
        }
      }
    };
    void loadUnread();
    const timer = window.setInterval(() => {
      void loadUnread();
    }, 60000);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [authed, location.pathname]);

  useEffect(() => {
    if (!authed || location.pathname === '/login' || robotInitChecked) {
      return;
    }
    let mounted = true;
    api
      .listRobots()
      .then((robots) => {
        if (!mounted) return;
        setRobotInitChecked(true);
        if ((robots || []).length === 0 && location.pathname !== '/robots') {
          navigate('/robots', { replace: true });
        }
      })
      .catch(() => {
        if (mounted) {
          setRobotInitChecked(true);
        }
      });
    return () => {
      mounted = false;
    };
  }, [authed, location.pathname, navigate, robotInitChecked]);

  const items = useMemo(() => {
    const baseItems: any[] = [
      { key: '/dashboard', icon: <DashboardOutlined />, label: <Link to="/dashboard">控制台</Link> },
      { key: '/robot-info', icon: <InfoCircleOutlined />, label: <Link to="/robot-info">机器人信息</Link> },
      { key: '/robots', icon: <RobotOutlined />, label: <Link to="/robots">机器人配置</Link> },
      { key: '/logs', icon: <FileTextOutlined />, label: <Link to="/logs">消息监控</Link> },
      { key: '/command-tasks', icon: <ProfileOutlined />, label: <Link to="/command-tasks">指令任务查询</Link> },
      { key: '/forward', icon: <ShareAltOutlined />, label: <Link to="/forward">消息转发</Link> },
      { key: '/providers', icon: <ApiOutlined />, label: <Link to="/providers">AI回复引擎</Link> }
    ];
    if (enableTroubleshoot && isAdmin) {
      baseItems.push({ key: '/troubleshoot', icon: <SearchOutlined />, label: <Link to="/troubleshoot">机器人排查</Link> });
    }
    if (isAdmin) {
      baseItems.push({ type: 'divider' });
      baseItems.push({ key: '/users', icon: <TeamOutlined />, label: <Link to="/users">用户管理</Link> });
      baseItems.push({ key: '/inbox-admin', icon: <NotificationOutlined />, label: <Link to="/inbox-admin">站内信配置</Link> });
      if (enableAdminIpBlacklist) {
        baseItems.push({ key: '/ip-blacklist', icon: <StopOutlined />, label: <Link to="/ip-blacklist">黑名单管理</Link> });
      }
      if (enableAdminEnterpriseAuth) {
        baseItems.push({ key: '/enterprise-authorization', icon: <BuildOutlined />, label: <Link to="/enterprise-authorization">企业定制开通</Link> });
      }
    }
    return baseItems;
  }, [enableTroubleshoot, isAdmin, enableAdminIpBlacklist, enableAdminEnterpriseAuth]);

  if (!authReady) {
    return null;
  }

  if (authed && !robotInitChecked && location.pathname !== '/login') {
    return null;
  }

  if (!authed && location.pathname !== '/login') {
    if (getAccessToken()) {
      return null;
    }
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }

  if (location.pathname === '/login') {
    return <LoginPage />;
  }

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider width={228} className="app-sider">
        <div className="app-sider-inner">
          <div>
            <div className="brand">WorkTool Console</div>
            <Menu className="app-menu" theme="light" mode="inline" selectedKeys={[location.pathname]} items={items} />
          </div>
          <div className="app-sider-footer">
            <Button
              type="default"
              icon={<BellOutlined />}
              block
              onClick={() => navigate('/inbox')}
            >
              <Badge count={inboxUnreadCount} overflowCount={99} offset={[10, 2]}>
                <span>站内信</span>
              </Badge>
            </Button>
            <Typography.Text type="secondary">账号：{maskPhone(userPhone)}</Typography.Text>
          </div>
        </div>
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Header className="topbar">
          <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
            <Typography.Title level={4} style={{ margin: 0, color: '#304047' }}>
              机器人管理系统
            </Typography.Title>
            <Space>
              <Button href="/docs/" target="_blank" rel="noreferrer">
                使用文档
              </Button>
              <Button href="https://worktool.apifox.cn/" target="_blank" rel="noreferrer">
                API文档
              </Button>
              <Button href="https://github.com/answerlink/openclaw-worktool-bridge" target="_blank" rel="noreferrer">
                开源地址
              </Button>
              <Button
                onClick={async () => {
                  try {
                    await api.authLogoutAll();
                  } catch {
                    // ignore remote logout errors
                  }
                  clearAccessToken();
                  window.location.href = '/login';
                }}
              >
                退出登录
              </Button>
            </Space>
          </div>
        </Header>
        <Content className="content-wrap">
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/robot-info" element={<RobotInfoPage />} />
            <Route path="/robots" element={<RobotPage />} />
            <Route path="/logs" element={<MessageLogPage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/command-tasks" element={<CommandTaskPage />} />
            <Route path="/forward" element={<ForwardPage />} />
            <Route path="/providers" element={<AIHubPage />} />
            {enableTroubleshoot && isAdmin ? <Route path="/troubleshoot" element={<TroubleshootPage />} /> : <Route path="/troubleshoot" element={<Navigate to="/dashboard" replace />} />}
            {isAdmin ? <Route path="/users" element={<UserManagementPage />} /> : <Route path="/users" element={<Navigate to="/dashboard" replace />} />}
            {isAdmin ? <Route path="/inbox-admin" element={<InboxAdminPage />} /> : <Route path="/inbox-admin" element={<Navigate to="/dashboard" replace />} />}
            {isAdmin && enableAdminIpBlacklist ? <Route path="/ip-blacklist" element={<IpBlacklistPage />} /> : <Route path="/ip-blacklist" element={<Navigate to="/dashboard" replace />} />}
            {isAdmin && enableAdminEnterpriseAuth ? <Route path="/enterprise-authorization" element={<EnterpriseAuthorizationPage />} /> : <Route path="/enterprise-authorization" element={<Navigate to="/dashboard" replace />} />}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

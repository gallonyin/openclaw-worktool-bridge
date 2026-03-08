import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Layout, Menu, Typography } from 'antd';
import {
  DashboardOutlined,
  RobotOutlined,
  FileTextOutlined,
  ApiOutlined,
  InfoCircleOutlined,
  SearchOutlined
} from '@ant-design/icons';
import DashboardPage from './pages/DashboardPage';
import RobotPage from './pages/RobotPage';
import MessageLogPage from './pages/MessageLogPage';
import AIHubPage from './pages/AIHubPage';
import RobotInfoPage from './pages/RobotInfoPage';
import TroubleshootPage from './pages/TroubleshootPage';
import { api } from './api';

const { Header, Sider, Content } = Layout;

export default function App() {
  const location = useLocation();
  const [enableTroubleshoot, setEnableTroubleshoot] = useState(false);

  useEffect(() => {
    let mounted = true;
    api
      .health()
      .then((d) => {
        if (mounted) {
          setEnableTroubleshoot(Boolean(d?.enable_troubleshoot));
        }
      })
      .catch(() => {
        if (mounted) {
          setEnableTroubleshoot(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  const items = useMemo(() => {
    const baseItems = [
      { key: '/dashboard', icon: <DashboardOutlined />, label: <Link to="/dashboard">控制台</Link> },
      { key: '/robot-info', icon: <InfoCircleOutlined />, label: <Link to="/robot-info">机器人信息</Link> },
      { key: '/robots', icon: <RobotOutlined />, label: <Link to="/robots">机器人配置</Link> },
      { key: '/logs', icon: <FileTextOutlined />, label: <Link to="/logs">消息监控</Link> },
      { key: '/providers', icon: <ApiOutlined />, label: <Link to="/providers">API 仓库</Link> }
    ];
    if (enableTroubleshoot) {
      baseItems.push({ key: '/troubleshoot', icon: <SearchOutlined />, label: <Link to="/troubleshoot">机器人排查</Link> });
    }
    return baseItems;
  }, [enableTroubleshoot]);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220}>
        <div className="brand">WorkTool Console</div>
        <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={items} />
      </Sider>
      <Layout>
        <Header className="topbar">
          <Typography.Title level={4} style={{ margin: 0 }}>机器人管理系统</Typography.Title>
        </Header>
        <Content className="content-wrap">
          <Routes>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/robot-info" element={<RobotInfoPage />} />
            <Route path="/robots" element={<RobotPage />} />
            <Route path="/logs" element={<MessageLogPage />} />
            <Route path="/providers" element={<AIHubPage />} />
            {enableTroubleshoot ? <Route path="/troubleshoot" element={<TroubleshootPage />} /> : null}
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

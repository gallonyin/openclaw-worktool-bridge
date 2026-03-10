import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Input, Select, Space, Table, Tag, Typography, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Robot } from '../types';
import { getLastSelectedRobotId, setLastSelectedRobotId } from '../robotSelection';

interface QaLogItem {
  robotId: string;
  startTime: string;
  timeCost: number;
  groupName: string;
  receivedName: string;
  roomType: number;
  textType: number;
  openThirdParty: number;
  url: string;
  rawSpoken: string;
  question: string;
  answer: string;
  messageId: string;
  atMe?: boolean;
}

const roomTypeMap: Record<number, string> = {
  1: '外部群',
  2: '外部联系人',
  3: '内部群',
  4: '内部联系人'
};

export default function MessageLogPage() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [robotsLoaded, setRobotsLoaded] = useState(false);
  const [robotId, setRobotId] = useState<string | undefined>(() => getLastSelectedRobotId());
  const [nameKeyword, setNameKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [logs, setLogs] = useState<QaLogItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  const selectRobot = (nextRobotId?: string) => {
    setRobotId(nextRobotId);
    setLastSelectedRobotId(nextRobotId);
  };

  const robotOptions = useMemo(
    () => robots.map((r) => ({ label: r.name ? `${r.name} (${r.robot_id})` : r.robot_id, value: r.robot_id })),
    [robots]
  );

  const loadRobots = async () => {
    const items = await api.listRobots();
    setRobots(items);
    setRobotsLoaded(true);
  };

  const loadLogs = async (nextPage = page, nextPageSize = pageSize) => {
    if (!robotId) return;
    setLoading(true);
    try {
      const res = await api.getWorktoolQaLogs({
        robot_id: robotId,
        page: nextPage,
        size: nextPageSize,
        sort: 'start_time,desc',
        name: nameKeyword.trim() || undefined
      });
      const data = res?.data || {};
      setLogs(data.list || []);
      setTotal(data.total || 0);
      setPage(data.pageNum || nextPage);
      setPageSize(data.pageSize || nextPageSize);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '拉取消息监控失败');
      setLogs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRobots();
  }, []);

  useEffect(() => {
    if (!robotsLoaded) return;
    if (robots.length === 0) {
      selectRobot(undefined);
      return;
    }
    const current = robotId;
    const exists = current && robots.some((x: Robot) => x.robot_id === current);
    if (!exists) {
      selectRobot(robots[0].robot_id);
    }
  }, [robotsLoaded, robots, robotId]);

  useEffect(() => {
    void loadLogs(1, pageSize);
  }, [robotId]);

  return (
    <Card
      title="消息监控（WorkTool 回调记录）"
      extra={(
        <Button icon={<ReloadOutlined />} onClick={() => loadLogs(page, pageSize)}>
          刷新
        </Button>
      )}
    >
      <Space style={{ marginBottom: 12 }}>
        <Select
          style={{ width: 340 }}
          value={robotId}
          onChange={selectRobot}
          options={robotOptions}
          placeholder="选择机器人"
          showSearch
          optionFilterProp="label"
        />
        <Input
          style={{ width: 260 }}
          placeholder="聊天对象筛选（name）"
          value={nameKeyword}
          onChange={(e) => setNameKeyword(e.target.value)}
          onPressEnter={() => loadLogs(1, pageSize)}
        />
        <Button onClick={() => loadLogs(1, pageSize)}>查询</Button>
      </Space>

      <Table
        rowKey={(r, idx) => `${r.messageId || 'no-id'}-${r.startTime}-${idx}`}
        loading={loading}
        dataSource={logs}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            void loadLogs(p, ps);
          }
        }}
        columns={[
          { title: '时间', dataIndex: 'startTime', width: 180 },
          { title: '机器人', dataIndex: 'robotId', width: 120 },
          { title: '群名', dataIndex: 'groupName', width: 180, ellipsis: true },
          { title: '提问者', dataIndex: 'receivedName', width: 120 },
          {
            title: '房间类型',
            dataIndex: 'roomType',
            width: 120,
            render: (v: number) => roomTypeMap[v] || String(v)
          },
          {
            title: '消息类型',
            dataIndex: 'textType',
            width: 100,
            render: (v: number) => <Tag>{v}</Tag>
          },
          {
            title: '是否@',
            dataIndex: 'atMe',
            width: 100,
            render: (v: boolean | undefined) => (v === undefined ? '-' : v ? '是' : '否')
          },
          { title: '问题', dataIndex: 'question', width: 280, ellipsis: true },
          { title: '回答', dataIndex: 'answer', width: 280, ellipsis: true },
          {
            title: '耗时(秒)',
            dataIndex: 'timeCost',
            width: 100,
            render: (v: number) => (v ?? 0).toFixed(3)
          },
          { title: 'messageId', dataIndex: 'messageId', width: 220, ellipsis: true },
          {
            title: '回调地址',
            dataIndex: 'url',
            width: 260,
            render: (v: string) => (
              <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 240 }}>
                {v}
              </Typography.Text>
            )
          }
        ]}
        scroll={{ x: 2200 }}
      />
    </Card>
  );
}

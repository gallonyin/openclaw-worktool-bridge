import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Checkbox, Input, Popover, Select, Space, Table, Tag, Typography, message } from 'antd';
import { ReloadOutlined, SettingOutlined } from '@ant-design/icons';
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

const COLUMN_PREF_KEY = 'message_monitor_visible_columns_v1';
const DEFAULT_VISIBLE_COLUMNS = [
  'startTime',
  'robotId',
  'groupName',
  'receivedName',
  'roomType',
  'textType',
  'atMe',
  'question',
  'answer',
  'timeCost',
  'messageId',
  'url',
];

export default function MessageLogPage() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [robotsLoaded, setRobotsLoaded] = useState(false);
  const [robotId, setRobotId] = useState<string | undefined>(() => getLastSelectedRobotId());
  const [sceneFilter, setSceneFilter] = useState<'all' | 'group' | 'private'>('all');
  const [nameKeyword, setNameKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [logs, setLogs] = useState<QaLogItem[]>([]);
  const [source, setSource] = useState<'local' | 'worktool'>('worktool');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [visibleColumns, setVisibleColumns] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(COLUMN_PREF_KEY);
      if (!raw) return DEFAULT_VISIBLE_COLUMNS;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed.filter((x) => typeof x === 'string');
      }
    } catch {
      // ignore
    }
    return DEFAULT_VISIBLE_COLUMNS;
  });

  const selectRobot = (nextRobotId?: string) => {
    setRobotId(nextRobotId);
    setLastSelectedRobotId(nextRobotId);
  };

  const robotOptions = useMemo(
    () => robots.map((r) => ({ label: r.name ? `${r.name} (${r.robot_id})` : r.robot_id, value: r.robot_id })),
    [robots]
  );

  const loadRobots = async () => {
    try {
      const items = await api.listRobots();
      setRobots(items);
      setRobotsLoaded(true);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载机器人失败，请先到“机器人配置”页确认已添加机器人后重试。');
      setRobots([]);
      setRobotsLoaded(true);
    }
  };

  const loadLogs = async (nextPage = page, nextPageSize = pageSize) => {
    if (!robotId) return;
    setLoading(true);
    try {
      const res = await api.getMessageMonitorLogs({
        robot_id: robotId,
        page: nextPage,
        size: nextPageSize,
        sort: 'start_time,desc',
        scene: sceneFilter,
        name: nameKeyword.trim() || undefined
      });
      const data = res?.data || {};
      const src = res?.source === 'local' ? 'local' : 'worktool';
      setSource(src);
      setLogs(data.list || []);
      setTotal(data.total || 0);
      setPage(data.pageNum || nextPage);
      setPageSize(data.pageSize || nextPageSize);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '拉取消息监控失败，请先确认已绑定消息回调且机器人有实际会话消息。');
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
  }, [robotId, sceneFilter]);

  useEffect(() => {
    try {
      localStorage.setItem(COLUMN_PREF_KEY, JSON.stringify(visibleColumns));
    } catch {
      // ignore
    }
  }, [visibleColumns]);

  const allColumns = useMemo(
    () => [
      { key: 'startTime', title: '时间', dataIndex: 'startTime', width: 180 },
      { key: 'robotId', title: '机器人', dataIndex: 'robotId', width: 120 },
      {
        key: 'groupName',
        title: '群名',
        dataIndex: 'groupName',
        width: 180,
        ellipsis: true,
        render: (v: string, row: QaLogItem) => (row.roomType === 2 || row.roomType === 4 ? '-' : (v || '-'))
      },
      { key: 'receivedName', title: '提问者', dataIndex: 'receivedName', width: 120 },
      {
        key: 'roomType',
        title: '房间类型',
        dataIndex: 'roomType',
        width: 120,
        render: (v: number) => roomTypeMap[v] || String(v)
      },
      {
        key: 'textType',
        title: '消息类型',
        dataIndex: 'textType',
        width: 100,
        render: (v: number) => <Tag>{v}</Tag>
      },
      {
        key: 'atMe',
        title: '是否@',
        dataIndex: 'atMe',
        width: 100,
        render: (v: boolean | undefined) => (v === undefined ? '-' : v ? '是' : '否')
      },
      { key: 'question', title: '问题', dataIndex: 'question', width: 280, ellipsis: true },
      { key: 'answer', title: '回答', dataIndex: 'answer', width: 280, ellipsis: true },
      {
        key: 'timeCost',
        title: '耗时(秒)',
        dataIndex: 'timeCost',
        width: 100,
        render: (v: number) => (v ?? 0).toFixed(3)
      },
      { key: 'messageId', title: 'messageId', dataIndex: 'messageId', width: 220, ellipsis: true },
      {
        key: 'url',
        title: '回调地址',
        dataIndex: 'url',
        width: 260,
        render: (v: string) => (
          <Typography.Text ellipsis={{ tooltip: v }} style={{ maxWidth: 240 }}>
            {v}
          </Typography.Text>
        )
      }
    ],
    []
  );

  const tableColumns = useMemo(
    () => allColumns.filter((c) => visibleColumns.includes(String(c.key))),
    [allColumns, visibleColumns]
  );

  const columnOptions = useMemo(
    () => allColumns.map((c) => ({ label: String(c.title), value: String(c.key) })),
    [allColumns]
  );

  return (
    <Card
      title={(
        <Space direction="vertical" size={0}>
          <span>消息监控（{source === 'local' ? '本平台处理记录' : 'WorkTool 回调记录'}）</span>
          <Typography.Text type="secondary">查看机器人有没有按预期读消息</Typography.Text>
        </Space>
      )}
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
        <Select
          style={{ width: 120 }}
          value={sceneFilter}
          onChange={(v) => setSceneFilter(v as 'all' | 'group' | 'private')}
          options={[
            { label: '全部', value: 'all' },
            { label: '群聊', value: 'group' },
            { label: '私聊', value: 'private' }
          ]}
        />
        <Input
          style={{ width: 260 }}
          placeholder="聊天对象筛选（群名/提问者）"
          value={nameKeyword}
          onChange={(e) => setNameKeyword(e.target.value)}
          onPressEnter={() => loadLogs(1, pageSize)}
        />
        <Button onClick={() => loadLogs(1, pageSize)}>查询</Button>
        <Popover
          trigger="click"
          placement="bottomRight"
          content={(
            <Space direction="vertical" size={8}>
              <Checkbox.Group
                options={columnOptions}
                value={visibleColumns}
                onChange={(vals) => {
                  const next = (vals as string[]).filter(Boolean);
                  if (next.length === 0) {
                    message.warning('至少保留一列');
                    return;
                  }
                  setVisibleColumns(next);
                }}
              />
              <Button size="small" onClick={() => setVisibleColumns(DEFAULT_VISIBLE_COLUMNS)}>恢复默认</Button>
            </Space>
          )}
        >
          <Button icon={<SettingOutlined />}>显示列</Button>
        </Popover>
      </Space>
      <Alert
        type={source === 'local' ? 'success' : 'info'}
        showIcon
        style={{ marginBottom: 12 }}
        message={source === 'local' ? '当前展示：本平台消息监控库（包含AI回答）' : '当前展示：WorkTool 回调记录'}
        description={
          source === 'local'
            ? '检测到该机器人消息回调由本平台处理，列表中的“回答”会显示本平台实际AI回复结果。'
            : '该机器人消息回调未走本平台处理，系统将展示 WorkTool 原始回调记录。'
        }
      />

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
        columns={tableColumns}
        scroll={{ x: 2200 }}
      />
    </Card>
  );
}

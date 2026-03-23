import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Card, Modal, Select, Space, Table, Tag, Typography, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';

type InboxStatus = 'all' | 'unread' | 'read';

type InboxLevel = 'info' | 'warning' | 'error';

interface InboxRow {
  delivery_id: number;
  message_id: number;
  category: 'system' | 'ops';
  level: InboxLevel;
  title: string;
  content: string;
  is_read: boolean;
  read_at?: string | null;
  delivered_at: string;
  expire_at?: string | null;
}

const levelTag: Record<InboxLevel, { color: string; label: string }> = {
  info: { color: 'blue', label: '普通' },
  warning: { color: 'orange', label: '提醒' },
  error: { color: 'red', label: '严重' },
};

export default function InboxPage() {
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<InboxRow[]>([]);
  const [status, setStatus] = useState<InboxStatus>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);

  const loadUnread = async () => {
    try {
      const res = await api.inboxUnreadCount();
      setUnreadCount(Number(res?.count || 0));
    } catch {
      // ignore badge refresh errors
    }
  };

  const loadRows = async (nextPage = page, nextPageSize = pageSize) => {
    setLoading(true);
    try {
      const res = await api.inboxMessages({
        page: nextPage,
        page_size: nextPageSize,
        status,
      });
      setRows((res?.items || []) as InboxRow[]);
      setTotal(Number(res?.total || 0));
      setPage(Number(res?.page || nextPage));
      setPageSize(Number(res?.page_size || nextPageSize));
      await loadUnread();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '拉取站内信失败');
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRows(1, pageSize);
  }, [status]);

  useEffect(() => {
    void loadUnread();
    const timer = window.setInterval(() => {
      void loadUnread();
    }, 60000);
    return () => window.clearInterval(timer);
  }, []);

  const markRead = async (row: InboxRow) => {
    if (row.is_read) return;
    await api.inboxMarkRead(row.delivery_id);
  };

  const openDetail = async (row: InboxRow) => {
    try {
      await markRead(row);
      await loadUnread();
      Modal.info({
        title: row.title,
        width: 620,
        content: (
          <div style={{ whiteSpace: 'pre-wrap', maxHeight: 400, overflowY: 'auto' }}>
            {row.content}
          </div>
        ),
      });
      if (!row.is_read) {
        void loadRows(page, pageSize);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '打开站内信失败');
    }
  };

  const categoryLabel = (v: 'system' | 'ops') => (v === 'system' ? '系统' : '运营');

  const columns = useMemo(
    () => [
      {
        title: '状态',
        dataIndex: 'is_read',
        width: 88,
        render: (v: boolean) => (v ? <Tag>已读</Tag> : <Tag color="processing">未读</Tag>),
      },
      {
        title: '类型',
        dataIndex: 'category',
        width: 76,
        render: (v: 'system' | 'ops') => <Tag>{categoryLabel(v)}</Tag>,
      },
      {
        title: '级别',
        dataIndex: 'level',
        width: 90,
        render: (v: InboxLevel) => <Tag color={levelTag[v]?.color || 'default'}>{levelTag[v]?.label || v}</Tag>,
      },
      {
        title: '标题',
        dataIndex: 'title',
        ellipsis: true,
        render: (v: string, row: InboxRow) => (
          <Typography.Link onClick={() => openDetail(row)}>{v}</Typography.Link>
        ),
      },
      { title: '接收时间', dataIndex: 'delivered_at', width: 180 },
      { title: '过期时间', dataIndex: 'expire_at', width: 180, render: (v: string | null | undefined) => v || '-' },
    ],
    []
  );

  return (
    <Card
      title={(
        <Space>
          <span>站内信</span>
          <Badge count={unreadCount} overflowCount={99} />
        </Space>
      )}
      extra={(
        <Space>
          <Button
            onClick={async () => {
              try {
                await api.inboxReadAll();
                message.success('已全部标记已读');
                await loadRows(1, pageSize);
              } catch (e: any) {
                message.error(e?.response?.data?.detail || '操作失败');
              }
            }}
          >
            全部已读
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadRows(page, pageSize)}>
            刷新
          </Button>
        </Space>
      )}
    >
      <Space style={{ marginBottom: 12 }}>
        <Select
          style={{ width: 140 }}
          value={status}
          onChange={(v) => setStatus(v as InboxStatus)}
          options={[
            { label: '全部', value: 'all' },
            { label: '仅未读', value: 'unread' },
            { label: '仅已读', value: 'read' },
          ]}
        />
      </Space>
      <Table
        rowKey="delivery_id"
        loading={loading}
        columns={columns as any}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (p, s) => {
            void loadRows(p, s);
          },
        }}
      />
    </Card>
  );
}

import { useEffect, useState } from 'react';
import { Button, Card, Input, Space, Table, Tag, message } from 'antd';
import { api } from '../api';

interface AdminUserItem {
  id: number;
  phone: string;
  company_name?: string | null;
  created_at: string;
  last_login_at?: string | null;
  robot_ids: string[];
}

export default function UserManagementPage() {
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<AdminUserItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  const load = async (nextPage = page, nextPageSize = pageSize) => {
    setLoading(true);
    try {
      const res = await api.adminListUsers({
        phone: keyword.trim() || undefined,
        page: nextPage,
        page_size: nextPageSize
      });
      setItems(res?.items || []);
      setTotal(Number(res?.total || 0));
      setPage(Number(res?.page || nextPage));
      setPageSize(Number(res?.page_size || nextPageSize));
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '加载用户列表失败');
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(1, pageSize);
  }, []);

  return (
    <Card title="用户管理">
      <Space style={{ marginBottom: 12 }}>
        <Input
          style={{ width: 260 }}
          placeholder="手机号搜索"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => void load(1, pageSize)}
        />
        <Button onClick={() => void load(1, pageSize)}>查询</Button>
      </Space>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => void load(p, ps),
        }}
        columns={[
          { title: '手机号', dataIndex: 'phone', width: 150 },
          { title: '企业', dataIndex: 'company_name', render: (v: string | null | undefined) => v || '-', width: 180 },
          { title: '注册时间', dataIndex: 'created_at', width: 180 },
          { title: '最后登录', dataIndex: 'last_login_at', render: (v: string | null | undefined) => v || '-', width: 180 },
          {
            title: '绑定机器人',
            dataIndex: 'robot_ids',
            render: (vals: string[]) => (
              <Space wrap>
                {(vals || []).length ? (vals || []).map((x) => <Tag key={x}>{x}</Tag>) : '-'}
              </Space>
            )
          }
        ]}
      />
    </Card>
  );
}

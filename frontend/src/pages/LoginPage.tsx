import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button, Card, Form, Input, Modal, Space, Tabs, Typography, message } from 'antd';
import { api, setAccessToken } from '../api';

function isValidPhone(phone: string) {
  return /^1\d{10}$/.test(phone) && !phone.startsWith('170');
}

type TabKey = 'login' | 'register';

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [tab, setTab] = useState<TabKey>('login');
  const [submitting, setSubmitting] = useState(false);
  const [sending, setSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [resetOpen, setResetOpen] = useState(false);
  const [smsAuthEnabled, setSmsAuthEnabled] = useState(false);

  const [loginForm] = Form.useForm();
  const [registerForm] = Form.useForm();
  const [resetForm] = Form.useForm();

  const nextPath = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const next = (params.get('next') || '/dashboard').trim();
    return next.startsWith('/') ? next : '/dashboard';
  }, [location.search]);

  useEffect(() => {
    const loadAuthConfig = async () => {
      try {
        const res = await api.authConfig();
        setSmsAuthEnabled(!!res?.sms_auth_enabled);
      } catch {
        setSmsAuthEnabled(false);
      }
    };
    void loadAuthConfig();
  }, []);

  const startCooldown = () => {
    let sec = 60;
    setCooldown(sec);
    const timer = setInterval(() => {
      sec -= 1;
      setCooldown(sec);
      if (sec <= 0) {
        clearInterval(timer);
      }
    }, 1000);
  };

  const sendSms = async (scene: 'register' | 'reset_password') => {
    const form = scene === 'register' ? registerForm : resetForm;
    const phone = (form.getFieldValue('phone') || '').trim();
    if (!isValidPhone(phone)) {
      message.warning('请先输入有效手机号');
      return;
    }
    setSending(true);
    try {
      await api.authSendSms({ phone, scene });
      message.success('验证码已发送');
      startCooldown();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '发送失败');
    } finally {
      setSending(false);
    }
  };

  const submitLogin = async () => {
    const values = await loginForm.validateFields();
    setSubmitting(true);
    try {
      const res = await api.authLogin({ phone: values.phone, password: values.password });
      const token = res?.access_token || '';
      if (!token) {
        throw new Error('登录成功但未返回 token');
      }
      setAccessToken(token);
      message.success('登录成功');
      navigate(nextPath, { replace: true });
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '登录失败');
    } finally {
      setSubmitting(false);
    }
  };

  const submitRegister = async () => {
    const values = await registerForm.validateFields();
    setSubmitting(true);
    try {
      const res = await api.authRegister({
        phone: values.phone,
        sms_code: smsAuthEnabled ? values.sms_code : undefined,
        password: values.password,
        company_name: values.company_name || undefined
      });
      const token = res?.access_token || '';
      if (!token) {
        throw new Error('注册成功但未返回 token');
      }
      setAccessToken(token);
      message.success('注册成功，已自动登录');
      navigate(nextPath, { replace: true });
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '注册失败');
    } finally {
      setSubmitting(false);
    }
  };

  const submitReset = async () => {
    const values = await resetForm.validateFields();
    setSubmitting(true);
    try {
      await api.authResetPassword({
        phone: values.phone,
        sms_code: values.sms_code,
        new_password: values.new_password
      });
      message.success('密码重置成功，请使用新密码登录');
      setResetOpen(false);
      loginForm.setFieldsValue({ phone: values.phone });
      resetForm.resetFields(['sms_code', 'new_password']);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '重置失败');
    } finally {
      setSubmitting(false);
    }
  };

  const phoneRules = [
    { required: true, message: '请输入手机号' },
    {
      validator: (_: any, value: string) =>
        !value || isValidPhone(String(value)) ? Promise.resolve() : Promise.reject(new Error('手机号格式不合法'))
    }
  ];

  return (
    <div className="login-shell">
      <div className="login-bg-orb login-bg-orb-left" />
      <div className="login-bg-orb login-bg-orb-right" />
      <Card className="login-card" title="WorkTool Console">
        <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
          登录后管理你的机器人与规则
        </Typography.Paragraph>

        <Tabs
          activeKey={tab}
          onChange={(k) => setTab(k as TabKey)}
          items={[
            {
              key: 'login',
              label: '登录',
              children: (
                <Form form={loginForm} layout="vertical">
                  <Form.Item name="phone" label="手机号" rules={phoneRules}>
                    <Input placeholder="请输入11位手机号" maxLength={11} />
                  </Form.Item>
                  <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                    <Input.Password placeholder="请输入密码" />
                  </Form.Item>
                  <div style={{ textAlign: 'right', marginTop: -8, marginBottom: 10 }}>
                    {smsAuthEnabled ? (
                      <Button
                        type="link"
                        size="small"
                        style={{ padding: 0, color: '#98a2b3', fontSize: 12 }}
                        onClick={() => {
                          setResetOpen(true);
                          const phone = (loginForm.getFieldValue('phone') || '').trim();
                          if (phone) {
                            resetForm.setFieldValue('phone', phone);
                          }
                        }}
                      >
                        忘记密码
                      </Button>
                    ) : null}
                  </div>
                  <Button type="primary" block loading={submitting} onClick={submitLogin}>
                    登录
                  </Button>
                </Form>
              )
            },
            {
              key: 'register',
              label: '注册',
              children: (
                <Form form={registerForm} layout="vertical">
                  <Form.Item name="phone" label="手机号" rules={phoneRules}>
                    <Input placeholder="请输入11位手机号" maxLength={11} />
                  </Form.Item>
                  {smsAuthEnabled ? (
                    <Form.Item label="短信验证码" required>
                      <Space.Compact style={{ width: '100%' }}>
                        <Form.Item name="sms_code" noStyle rules={[{ required: true, message: '请输入验证码' }]}>
                          <Input placeholder="请输入6位验证码" maxLength={6} />
                        </Form.Item>
                        <Button onClick={() => void sendSms('register')} loading={sending} disabled={cooldown > 0}>
                          {cooldown > 0 ? `${cooldown}s` : '发送验证码'}
                        </Button>
                      </Space.Compact>
                    </Form.Item>
                  ) : null}
                  <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }, { min: 8, message: '至少8位' }]}>
                    <Input.Password placeholder="至少8位" />
                  </Form.Item>
                  <Form.Item name="company_name" label="企业名称（选填）">
                    <Input placeholder="可选" />
                  </Form.Item>
                  <Button type="primary" block loading={submitting} onClick={submitRegister}>
                    注册并登录
                  </Button>
                </Form>
              )
            }
          ]}
        />
      </Card>

      {smsAuthEnabled ? (
        <Modal
          title="重置密码"
          open={resetOpen}
          onCancel={() => setResetOpen(false)}
          onOk={() => void submitReset()}
          okText="确认重置"
          cancelText="取消"
          confirmLoading={submitting}
          destroyOnClose
        >
          <Form form={resetForm} layout="vertical">
            <Form.Item name="phone" label="手机号" rules={phoneRules}>
              <Input placeholder="请输入11位手机号" maxLength={11} />
            </Form.Item>
            <Form.Item label="短信验证码" required>
              <Space.Compact style={{ width: '100%' }}>
                <Form.Item name="sms_code" noStyle rules={[{ required: true, message: '请输入验证码' }]}>
                  <Input placeholder="请输入6位验证码" maxLength={6} />
                </Form.Item>
                <Button onClick={() => void sendSms('reset_password')} loading={sending} disabled={cooldown > 0}>
                  {cooldown > 0 ? `${cooldown}s` : '发送验证码'}
                </Button>
              </Space.Compact>
            </Form.Item>
            <Form.Item
              name="new_password"
              label="新密码"
              rules={[{ required: true, message: '请输入新密码' }, { min: 8, message: '至少8位' }]}
            >
              <Input.Password placeholder="至少8位" />
            </Form.Item>
          </Form>
        </Modal>
      ) : null}
    </div>
  );
}

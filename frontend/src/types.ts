export interface Robot {
  robot_id: string;
  name: string;
  private_chat_enabled: boolean;
  group_chat_enabled: boolean;
  group_reply_only_when_mentioned: boolean;
  created_at: string;
  updated_at: string;
}

export interface Provider {
  id: number;
  name: string;
  base_url: string;
  model?: string | null;
  provider_type: 'openai' | 'openclaw';
  auth_scheme: 'bearer' | 'x-openclaw-token' | 'none';
  extra_json?: string | null;
  enabled: boolean;
  api_token_masked: string;
}

export interface Rule {
  id: number;
  robot_id: string;
  scene: 'group' | 'private';
  pattern: string;
  provider_id: number;
  provider_name: string;
  priority: number;
  enabled: boolean;
}

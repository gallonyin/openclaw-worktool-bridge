export interface Robot {
  robot_id: string;
  name: string;
  private_chat_enabled: boolean;
  group_chat_enabled: boolean;
  group_reply_only_when_mentioned: boolean;
  group_reply_mode?: 'always' | 'mention_only' | 'ai_decide';
  group_decision_provider_id?: number | null;
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
  is_system?: boolean;
  can_manage?: boolean;
}

export interface Rule {
  id: number;
  robot_id: string;
  scene: 'group' | 'private';
  pattern_match_type?: 'all' | 'exact' | 'regex';
  pattern: string;
  content_match_type?: 'all' | 'exact' | 'regex';
  content_pattern?: string | null;
  provider_id: number;
  provider_name: string;
  priority: number;
  enabled: boolean;
}

export interface ForwardRule {
  id: number;
  source_robot_id: string;
  source_robot_name: string;
  source_scene: 'group' | 'private';
  source_match_type: 'all' | 'exact' | 'regex';
  source_pattern: string;
  target_name: string;
  use_other_robot: boolean;
  send_robot_id?: string | null;
  send_robot_name?: string | null;
  prefix_enabled: boolean;
  prefix_template: string;
  keyword_match_type: 'all' | 'exact' | 'regex';
  keyword_pattern: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ForwardLog {
  id: number;
  rule_id: number;
  source_robot_id: string;
  send_robot_id: string;
  source_scene: 'group' | 'private';
  source_name: string;
  sender_name: string;
  target_name: string;
  message_id: string;
  question_text: string;
  forwarded_text: string;
  status: 'success' | 'failed' | 'skipped';
  error_reason: string;
  time_cost: number;
  created_at: string;
}

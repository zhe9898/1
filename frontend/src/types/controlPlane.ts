export interface ControlActionField {
  key: string;
  label: string;
  input_type: string;
  required: boolean;
  placeholder: string | null;
  value: string | number | boolean | null;
}

export interface StatusView {
  key: string;
  label: string;
  tone: string;
}

export interface ControlAction {
  key: string;
  label: string;
  endpoint: string;
  method: string;
  enabled: boolean;
  requires_admin: boolean;
  reason: string | null;
  confirmation: string | null;
  fields: ControlActionField[];
}

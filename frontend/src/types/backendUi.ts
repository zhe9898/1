import type { ControlAction } from "@/types/controlPlane";

export interface FormFieldOption {
  value: string;
  label: string;
}

export interface FormFieldSchema {
  key: string;
  label: string;
  input_type: string;
  required: boolean;
  description: string | null;
  placeholder: string | null;
  value: string | number | boolean | null;
  options: FormFieldOption[];
}

export interface FormSectionSchema {
  id: string;
  label: string;
  description: string | null;
  fields: FormFieldSchema[];
}

export interface ResourceSchema {
  product: string;
  profile: string;
  runtime_profile: string;
  resource: string;
  title: string;
  description: string | null;
  empty_state: string | null;
  policies: Record<string, unknown>;
  submit_action: ControlAction | null;
  sections: FormSectionSchema[];
}

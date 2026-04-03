import { defineStore } from "pinia";
import { ref } from "vue";
import { EVALUATIONS } from "@/utils/api";
import { http } from "@/utils/http";
import type { ResourceSchema } from "@/types/backendUi";
import type { ControlAction, StatusView } from "@/types/controlPlane";
import { normalizeStatusView } from "@/utils/statusView";

export interface EvaluationItem {
  evaluation_id: string;
  software_id: string;
  branch: string;
  rating: number;
  category: string;
  comment: string | null;
  evaluator: string;
  status: string;
  status_view: StatusView;
  actions: ControlAction[];
  created_at: string;
  updated_at: string;
}

export interface CreateEvaluationPayload {
  evaluation_id: string;
  software_id: string;
  branch?: string;
  rating: number;
  category?: string;
  comment?: string | null;
}

function normalizeEvaluation(
  partial: Partial<EvaluationItem> & { evaluation_id: string }
): EvaluationItem {
  return {
    evaluation_id: partial.evaluation_id,
    software_id: partial.software_id ?? "",
    branch: partial.branch ?? "main",
    rating: partial.rating ?? 0,
    category: partial.category ?? "general",
    comment: partial.comment ?? null,
    evaluator: partial.evaluator ?? "",
    status: partial.status ?? "submitted",
    status_view:
      partial.status_view ??
      normalizeStatusView(null, partial.status ?? "submitted", partial.status ?? "Submitted"),
    actions: partial.actions ?? [],
    created_at: partial.created_at ?? new Date().toISOString(),
    updated_at: partial.updated_at ?? new Date().toISOString(),
  };
}

export const useEvaluationsStore = defineStore("evaluations", () => {
  const items = ref<EvaluationItem[]>([]);
  const schema = ref<ResourceSchema | null>(null);
  const loading = ref(false);
  const submitting = ref(false);
  const error = ref<string | null>(null);
  const lastUpdatedAt = ref(0);

  async function fetchSchema(): Promise<ResourceSchema | null> {
    try {
      const { data } = await http.get<ResourceSchema>(EVALUATIONS.schema);
      schema.value = data;
      return data;
    } catch (err: unknown) {
      error.value =
        err instanceof Error ? err.message : "Failed to load evaluation schema";
      return null;
    }
  }

  async function fetchEvaluations(
    query: Record<string, unknown> = {}
  ): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const params = Object.fromEntries(
        Object.entries(query)
          .map(([k, v]) => {
            if (typeof v === "string" && v) return [k, v];
            return null;
          })
          .filter((e): e is [string, string] => e != null)
      );
      const { data } = await http.get<EvaluationItem[]>(EVALUATIONS.list, {
        params,
      });
      items.value = data.map((item) => normalizeEvaluation(item));
      lastUpdatedAt.value = Date.now();
    } catch (err: unknown) {
      error.value =
        err instanceof Error ? err.message : "Failed to load evaluations";
    } finally {
      loading.value = false;
    }
  }

  async function createEvaluation(
    payload: CreateEvaluationPayload
  ): Promise<EvaluationItem | null> {
    submitting.value = true;
    error.value = null;
    try {
      const { data } = await http.post<EvaluationItem>(
        EVALUATIONS.create,
        payload
      );
      const normalized = normalizeEvaluation(data);
      items.value.unshift(normalized);
      lastUpdatedAt.value = Date.now();
      return normalized;
    } catch (err: unknown) {
      error.value =
        err instanceof Error ? err.message : "Failed to create evaluation";
      throw err;
    } finally {
      submitting.value = false;
    }
  }

  async function deleteEvaluation(evaluationId: string): Promise<void> {
    submitting.value = true;
    error.value = null;
    try {
      await http.delete(EVALUATIONS.delete(evaluationId));
      items.value = items.value.filter(
        (item) => item.evaluation_id !== evaluationId
      );
      lastUpdatedAt.value = Date.now();
    } catch (err: unknown) {
      error.value =
        err instanceof Error ? err.message : "Failed to delete evaluation";
      throw err;
    } finally {
      submitting.value = false;
    }
  }

  return {
    items,
    schema,
    loading,
    submitting,
    error,
    lastUpdatedAt,
    fetchSchema,
    fetchEvaluations,
    createEvaluation,
    deleteEvaluation,
  };
});

<template>
  <div class="card bg-base-100 shadow-xl mb-6">
    <div class="card-body">
      <h2 class="card-title justify-between">
        <span>账号与设备管理 (RBAC Domain)</span>
        <button
          class="btn btn-sm btn-primary"
          @click="showAddModal = true"
        >
          + 新增家属账号
        </button>
      </h2>

      <div
        v-if="loading"
        class="flex justify-center py-4"
      >
        <span class="loading loading-spinner text-primary" />
      </div>
      
      <div
        v-else-if="errorMsg"
        class="alert alert-error my-2"
      >
        {{ errorMsg }}
      </div>
      <div
        v-else-if="actionMsg"
        class="alert my-2"
        :class="actionOk ? 'alert-success' : 'alert-error'"
      >
        {{ actionMsg }}
      </div>

      <div
        v-else
        class="overflow-x-auto mt-4"
      >
        <table class="table w-full">
          <thead>
            <tr>
              <th>ID</th>
              <th>账号 (隔离域)</th>
              <th>角色</th>
              <th>状态</th>
              <th>安全设备 (通行密钥)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="user in users"
              :key="user.id"
            >
              <td>{{ user.id }}</td>
              <td>
                <div class="font-bold">
                  {{ user.username }} ({{ user.display_name || user.username }})
                </div>
                <div class="text-xs opacity-50">
                  Tenant: {{ user.tenant_id }}
                </div>
              </td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="user.role === 'admin' ? 'badge-primary' : 'badge-ghost'"
                >
                  {{ user.role }}
                </span>
              </td>
              <td>
                <span
                  v-if="user.has_password"
                  class="badge badge-success badge-sm"
                >已设密码</span>
                <span
                  v-else
                  class="badge badge-warning badge-sm"
                >未设密码</span>
              </td>
              <td>
                <div
                  v-if="user.webauthn_credentials.length === 0"
                  class="text-xs text-base-content/50"
                >
                  无绑定设备
                </div>
                <div
                  v-else
                  class="flex flex-col gap-1"
                >
                  <div
                    v-for="cred in user.webauthn_credentials"
                    :key="cred.id"
                    class="flex items-center justify-between bg-base-200 p-1 rounded"
                  >
                    <span
                      class="text-xs truncate w-24"
                      :title="cred.name"
                    >{{ cred.name || '未知设备' }}</span>
                    <button
                      class="btn btn-xs btn-ghost text-error"
                      title="吊销设备"
                      @click="revokeCredential(cred.id)"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              </td>
              <td>
                <button
                  v-if="user.role !== 'admin'"
                  class="btn btn-xs btn-outline btn-primary"
                  @click="generateInvite(user.id, user.display_name || user.username)"
                >
                  带外邀请
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Add User Modal -->
    <dialog
      class="modal"
      :class="{ 'modal-open': showAddModal }"
    >
      <div class="modal-box">
        <h3 class="font-bold text-lg mb-4">
          新建账号
        </h3>
        <form @submit.prevent="createUser">
          <div class="form-control mb-2">
            <label class="label"><span class="label-text">用户名 (登录用)</span></label>
            <input
              v-model="newUser.username"
              type="text"
              class="input input-bordered w-full"
              required
            >
          </div>
          <div class="form-control mb-2">
            <label class="label"><span class="label-text">显示名称</span></label>
            <input
              v-model="newUser.displayName"
              type="text"
              class="input input-bordered w-full"
            >
          </div>
          <div class="form-control mb-2">
            <label class="label"><span class="label-text">初始密码</span></label>
            <input
              v-model="newUser.password"
              type="password"
              class="input input-bordered w-full"
              required
              minlength="6"
            >
          </div>
          <div class="form-control mb-4">
            <label class="label"><span class="label-text">数据隔离域 (Tenant ID)</span></label>
            <input
              v-model="newUser.tenantId"
              type="text"
              placeholder="例如: family_share 或 private_wife"
              class="input input-bordered w-full"
              required
            >
            <label class="label"><span class="label-text-alt">用于底层 Postgres RLS 隔离隐私数据</span></label>
          </div>
          <div
            v-if="addErrorMsg"
            class="alert alert-error text-sm py-2 mb-4"
          >
            {{ addErrorMsg }}
          </div>
          <div class="modal-action">
            <button
              type="button"
              class="btn btn-ghost"
              @click="showAddModal = false"
            >
              取消
            </button>
            <button
              type="submit"
              class="btn btn-primary"
              :disabled="adding"
            >
              确认创建
            </button>
          </div>
        </form>
      </div>
      <form
        method="dialog"
        class="modal-backdrop"
        @click="showAddModal = false"
      >
        <button>close</button>
      </form>
    </dialog>

    <!-- Invite Link Modal -->
    <dialog
      class="modal"
      :class="{ 'modal-open': inviteLink }"
    >
      <div class="modal-box">
        <h3 class="font-bold text-lg mb-4">
          一次性物理绑定凭证
        </h3>
        <p class="py-2 text-sm text-base-content/80">
          已为 <strong>{{ inviteTargetName }}</strong> 生成专属邀请链接（限时15分钟）。<br>
          请通过微信/QQ等带外信道发送给对方，点击后即可调用本机生物特征完成物理绑定。
        </p>
        <div class="form-control">
          <textarea
            v-model="inviteLink"
            class="textarea textarea-bordered h-24 mb-4 select-all"
            readonly
          />
        </div>
        <div class="modal-action">
          <button
            class="btn btn-ghost"
            @click="inviteLink = ''"
          >
            取消
          </button>
          <button
            class="btn btn-primary"
            @click="copyInviteLink"
          >
            复制并关闭
          </button>
        </div>
      </div>
    </dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * @generated by ZEN70-AI-Agent
 * @architecture-version V2.0
 * @compliance strict-sre-ruleset
 * @description 用户与设备管理 RBAC 面板。ADR 0015 强制使用 http 实例。
 */
import { ref, onMounted } from 'vue';
import type { UserSummary } from '@/types/user';
import { http } from '@/utils/http';
import { extractAxiosError } from '@/utils/errorMessage';
import { AUTH } from '@/utils/api';


const users = ref<UserSummary[]>([]);
const loading = ref(true);
const errorMsg = ref("");

const showAddModal = ref(false);
const adding = ref(false);
const addErrorMsg = ref("");
const newUser = ref({ username: "", displayName: "", password: "", tenantId: "" });

const inviteLink = ref("");
const inviteTargetName = ref("");
const actionMsg = ref("");
const actionOk = ref(false);

async function fetchUsers(): Promise<void> {
  loading.value = true;
  errorMsg.value = "";
  try {
    const { data } = await http.get<{ users: UserSummary[] }>(AUTH.users);
    users.value = data.users;
  } catch (err: unknown) {
    errorMsg.value = extractAxiosError(err, "加载用户列表失败");
  } finally {
    loading.value = false;
  }
}

async function createUser(): Promise<void> {
  adding.value = true;
  addErrorMsg.value = "";
  try {
    const payload = {
      username: newUser.value.username,
      display_name: newUser.value.displayName,
      password: newUser.value.password,
      role: "family",
      tenant_id: newUser.value.tenantId,
    };

    await http.post(AUTH.users, payload);

    showAddModal.value = false;
    newUser.value = { username: "", displayName: "", password: "", tenantId: "" };
    await fetchUsers();
  } catch (err: unknown) {
    addErrorMsg.value = extractAxiosError(err, "创建用户失败");
  } finally {
    adding.value = false;
  }
}

async function revokeCredential(credId: string): Promise<void> {
  if (!confirm("确定要吊销该设备通行密钥吗？吊销后该设备将无法免密登录。")) return;
  try {
    await http.delete(AUTH.removeCredential(credId));
    await fetchUsers();
    actionOk.value = true;
    actionMsg.value = "设备通行密钥已吊销";
  } catch (err: unknown) {
    actionOk.value = false;
    actionMsg.value = "吊销失败: " + extractAxiosError(err, "未知错误");
  }
}

async function generateInvite(userId: number, name: string): Promise<void> {
  try {
    const { data } = await http.post<{ token: string }>(AUTH.invites, {
      user_id: userId,
      expires_in_minutes: 15,
    });

    const baseUrl = window.location.origin;
    inviteLink.value = `${baseUrl}/invite?token=${data.token}`;
    inviteTargetName.value = name;
  } catch (err: unknown) {
    actionOk.value = false;
    actionMsg.value = "生成邀请失败: " + extractAxiosError(err, "未知错误");
  }
}

async function copyInviteLink(): Promise<void> {
  try {
    await navigator.clipboard.writeText(inviteLink.value);
  } catch {
    // fallback if clipboard fails — 非关键路径静默降级
  }
  inviteLink.value = "";
}

onMounted(() => {
  void fetchUsers();
});
</script>

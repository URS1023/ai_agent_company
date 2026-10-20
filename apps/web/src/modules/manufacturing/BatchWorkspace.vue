<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
const session = useSession();
const list = useCollection(() => "/product-batches");
const op = useOperation();
const products = ref<Entity[]>([]);
const dialog = ref(false);
const selected = ref<Entity | null>(null);
const form = reactive({
  name: "",
  product_id: "",
  external_ref: "",
  quantity: undefined as number | undefined,
});
function edit(record: Entity | null) {
  selected.value = record;
  Object.assign(form, {
    name: record?.name ?? "",
    product_id: record?.product_id ?? "",
    external_ref: record?.external_ref ?? "",
    quantity: record?.quantity ?? undefined,
  });
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/product-batches/${selected.value.id}`, {
        ...form,
        revision: selected.value.revision,
      });
    else await api.post("/product-batches", form);
    dialog.value = false;
  }, "批次已保存");
  await list.load();
}
async function remove(record: Entity) {
  try {
    await ElMessageBox.confirm(`删除批次“${record.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`/product-batches/${record.id}`, record.revision),
      "批次已删除",
    );
    await list.load();
  } catch {}
}
onMounted(async () => {
  await list.load();
  await op.run(async () => {
    products.value = (await api.get<ListResult>("/products?limit=200"))
      .items as Entity[];
  });
});
</script>
<template>
  <div class="page-stack">
    <section class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="批次名称"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button
      ><span class="spacer"></span
      ><el-button
        v-if="session.can('m11.write')"
        type="primary"
        @click="edit(null)"
        >新增批次</el-button
      >
    </section>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          prop="name"
          label="批次名称"
          min-width="220"
        /><el-table-column label="产品" min-width="200"
          ><template #default="{ row }">{{
            products.find((item) => item.id === row.product_id)?.name ??
            row.product_id
          }}</template></el-table-column
        ><el-table-column
          prop="external_ref"
          label="来源批次号"
          min-width="180"
        /><el-table-column
          prop="quantity"
          label="数量"
          width="100"
        /><el-table-column label="操作" width="150"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m11.write')"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ><el-button
              v-if="session.can('m11.delete')"
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      v-model="dialog"
      :title="selected ? '编辑批次' : '新增批次'"
      width="640px"
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="批次名称" :error="op.fields.value.name"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item label="产品" :error="op.fields.value.product_id"
            ><el-select
              v-model="form.product_id"
              :disabled="!!selected"
              filterable
              ><el-option
                v-for="product in products"
                :key="product.id"
                :value="product.id"
                :label="product.name" /></el-select></el-form-item
          ><el-form-item label="来源批次号"
            ><el-input v-model="form.external_ref" /></el-form-item
          ><el-form-item label="数量"
            ><el-input-number v-model="form.quantity" :min="1" :precision="0"
          /></el-form-item>
        </div>
        <el-button
          type="primary"
          native-type="submit"
          :loading="op.busy.value"
          :disabled="!form.product_id"
          >保存批次</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>

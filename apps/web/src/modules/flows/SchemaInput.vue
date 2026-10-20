<script setup lang="ts">
import { computed } from "vue";
import { initialSchemaValue, type SimpleSchema } from "./graphInputs";
const props = defineProps<{
  schema: SimpleSchema;
  label: string;
  path: string;
  required?: boolean;
  errors: Record<string, string>;
  disabled?: boolean;
}>();
const model = defineModel<unknown>();
const objectValue = computed(
  () =>
    (model.value &&
    typeof model.value === "object" &&
    !Array.isArray(model.value)
      ? model.value
      : {}) as Record<string, unknown>,
);
const arrayValue = computed(() =>
  Array.isArray(model.value) ? model.value : [],
);
const maximum = computed(() => Math.min(props.schema.maxItems ?? 100, 100));
function update(key: string, value: unknown) {
  const next = { ...objectValue.value };
  if (value === undefined) delete next[key];
  else
    Object.defineProperty(next, key, {
      value,
      enumerable: true,
      configurable: true,
      writable: true,
    });
  model.value = next;
}
function property(key: string) {
  return Object.prototype.hasOwnProperty.call(objectValue.value, key)
    ? objectValue.value[key]
    : undefined;
}
function updateItem(index: number, value: unknown) {
  model.value = arrayValue.value.map((item, position) =>
    position === index ? value : item,
  );
}
function add() {
  model.value = [...arrayValue.value, initialSchemaValue(props.schema.items!)];
}
function remove(index: number) {
  model.value = arrayValue.value.filter((_, position) => position !== index);
}
</script>
<template>
  <el-form-item
    :label="label"
    :required="required"
    :error="errors[path]"
    class="schema-input"
  >
    <template v-if="schema.type === 'object' || schema.type === 'array'">
      <div class="schema-group">
        <el-button
          v-if="model === undefined"
          :disabled="disabled"
          @click="model = initialSchemaValue(schema)"
          >填写{{ label }}</el-button
        >
        <template v-else>
          <div v-if="!required" class="row-actions">
            <el-button text :disabled="disabled" @click="model = undefined"
              >清除</el-button
            >
          </div>
          <template v-if="schema.type === 'object'"
            ><SchemaInput
              v-for="(child, key) in schema.properties ?? {}"
              :key="key"
              :schema="child"
              :model-value="property(String(key))"
              :label="child.title || String(key)"
              :path="`${path}.${String(key)}`"
              :required="schema.required?.includes(String(key))"
              :errors="errors"
              :disabled="disabled"
              @update:model-value="update(String(key), $event)"
          /></template>
          <template v-else
            ><div
              v-for="(item, index) in arrayValue"
              :key="index"
              class="schema-array-item"
            >
              <SchemaInput
                :schema="schema.items!"
                :model-value="item"
                :label="`第${index + 1}项`"
                :path="`${path}.${index}`"
                required
                :errors="errors"
                :disabled="disabled"
                @update:model-value="updateItem(index, $event)"
              /><el-button
                text
                type="danger"
                :disabled="disabled"
                @click="remove(index)"
                >移除</el-button
              >
            </div>
            <el-button
              :disabled="disabled || arrayValue.length >= maximum"
              @click="add"
              >添加一项</el-button
            ></template
          >
        </template>
      </div>
    </template>
    <el-select
      v-else-if="schema.enum"
      :model-value="model"
      :aria-label="label"
      clearable
      :disabled="disabled"
      @update:model-value="model = $event"
      @clear="model = undefined"
      ><el-option
        v-for="(value, index) in schema.enum"
        :key="index"
        :value="value ?? ''"
        :label="String(value)"
    /></el-select>
    <el-select
      v-else-if="schema.type === 'boolean'"
      :model-value="model"
      :aria-label="label"
      clearable
      :disabled="disabled"
      @update:model-value="model = $event"
      @clear="model = undefined"
      ><el-option :value="true" label="是" /><el-option
        :value="false"
        label="否"
    /></el-select>
    <el-input-number
      v-else-if="schema.type === 'number' || schema.type === 'integer'"
      :model-value="model as number | undefined"
      :aria-label="label"
      :disabled="disabled"
      :min="schema.minimum"
      :max="schema.maximum"
      :controls="false"
      @update:model-value="model = $event"
    />
    <el-input
      v-else
      :model-value="model as string | undefined"
      :aria-label="label"
      :type="schema.multiline ? 'textarea' : 'text'"
      :rows="3"
      :maxlength="schema.maxLength ?? 40000"
      :disabled="disabled"
      @update:model-value="model = $event"
    />
  </el-form-item>
</template>
<style scoped>
.schema-group {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: var(--sp-3);
}
.schema-array-item {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
}
.schema-array-item > .schema-input {
  flex: 1;
  min-width: 0;
}
</style>

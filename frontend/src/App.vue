<script setup lang="ts"></script>

<template>
  <RouterView />
</template>

<style>
:root {
  /* 覆写 Element Plus 主色为项目绿色 #1a7f37 */
  --el-color-primary: #1a7f37;
  --el-color-primary-light-3: #5fa573;
  --el-color-primary-light-5: #8dbf9b;
  --el-color-primary-light-7: #bad9c3;
  --el-color-primary-light-8: #d1e5d7;
  --el-color-primary-light-9: #e8f2eb;
  --el-color-primary-dark-2: #15662c;
}

body {
  margin: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: #fafafa;
}
</style>

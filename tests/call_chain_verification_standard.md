# PyGitNexus 调用链验证标准 — 8 个典型业务方法

> 生成时间: 2026-05-06
> 数据源: newbee-mall (88 Java 文件) KuzuDB + tree-sitter AST 地面真相
> 选取原则: 业务接口、多上游多下游优先、完整 Controller→Service→Mapper 链路

---

## 选取的 8 个方法

| # | 方法 | 业务含义 | 上游调用者 | 下游被调用 | 2级嵌套 |
|---|------|---------|-----------|-----------|---------|
| 1 | `NewBeeMallShoppingCartService.getMyShoppingCartItems` | 购物车查询 | **3** (多调用者最佳) | 11 | 1 |
| 2 | `NewBeeMallOrderService.saveOrder` | 创建订单 | 1 | **24** (最深) | 3 |
| 3 | `NewBeeMallGoodsService.updateNewBeeMallGoods` | 商品更新 | 1 | 15 | 0 |
| 4 | `NewBeeMallShoppingCartService.saveNewBeeMallCartItem` | 添加购物车 | 1 | 9 (含内调) | 1 |
| 5 | `NewBeeMallOrderService.paySuccess` | 支付成功 | 1 | 8 | 0 |
| 6 | `NewBeeMallCategoryService.getCategoriesForSearch` | 分类查询 | 1 | 9 | 0 |
| 7 | `NewBeeMallGoodsService.searchNewBeeMallGoods` | 商品搜索 | 1 | 9 | 1 |
| 8 | `NewBeeMallUserService.updateUserInfo` | 用户信息更新 | 1 | 11 | 1 |

---

## 逐方法验证

### 1. NewBeeMallShoppingCartService.getMyShoppingCartItems

**Impl**: `NewBeeMallShoppingCartServiceImpl.getMyShoppingCartItems`
**上游** (3 调用者):
- `[controller] ShoppingCartController.cartListPage`
- `[controller] ShoppingCartController.settlePage`
- `[controller] OrderController.saveOrder`

**下游** (11 被调用):
```
NewBeeMallShoppingCartItemVO.setSellingPrice
NewBeeMallShoppingCartItemVO.setGoodsCoverImg
NewBeeMallShoppingCartItemVO.setGoodsName
NewBeeMallShoppingCartItemMapper.selectByUserId
NewBeeMallShoppingCartItem.getGoodsId
NewBeeMallGoodsMapper.selectByPrimaryKeys
NewBeeMallGoods.getSellingPrice
NewBeeMallGoods.getGoodsName
NewBeeMallGoods.getGoodsCoverImg
NewBeeMallGoods.getGoodsId
BeanUtil.copyProperties
```

**KuzuDB**: Precision 100% Recall 100% ✅ 完全匹配

---

### 2. NewBeeMallOrderService.saveOrder

**Impl**: `NewBeeMallOrderServiceImpl.saveOrder`
**上游** (1 调用者):
- `[controller] OrderController.saveOrder`

**下游** (24 被调用):
```
NewBeeMallShoppingCartItemVO.getSellingPrice
NewBeeMallShoppingCartItemVO.getCartItemId
NewBeeMallShoppingCartItemVO.getGoodsId
NewBeeMallShoppingCartItemVO.getGoodsCount
NewBeeMallUserVO.getUserId
NewBeeMallUserVO.getAddress
NewBeeMallShoppingCartItemMapper.deleteBatch
NewBeeMallGoodsMapper.selectByPrimaryKeys
NewBeeMallGoodsMapper.updateStockNum
NewBeeMallOrderMapper.insertSelective
NewBeeMallOrderItemMapper.insertBatch
BeanUtil.copyList
BeanUtil.copyProperties
NumberUtil.genOrderNo
NewBeeMallGoods.getGoodsSellStatus
NewBeeMallGoods.getGoodsId
NewBeeMallException.fail
NewBeeMallOrder.setOrderNo
NewBeeMallOrder.setUserId
NewBeeMallOrder.setUserAddress
NewBeeMallOrder.setTotalPrice
NewBeeMallOrder.getOrderId
NewBeeMallOrder.setExtraInfo
NewBeeMallOrderItem.setOrderId
```

**KuzuDB**: Precision 91.3% Recall 87.5%
- FP(2): `genOrderNo`, `getCartItemId` — KuzuDB 有但 AST 中不存在
- FN(3): `getGoodsName`, `getResult`, `getStockNum` — AST 有但 KuzuDB 漏了

---

### 3. NewBeeMallGoodsService.updateNewBeeMallGoods

**Impl**: `NewBeeMallGoodsServiceImpl.updateNewBeeMallGoods`
**上游** (1 调用者):
- `[controller] NewBeeMallGoodsController.update`

**下游** (15 被调用):
```
NewBeeMallGoodsMapper.selectByPrimaryKey
NewBeeMallGoodsMapper.updateByPrimaryKeySelective
NewBeeMallGoodsMapper.selectByCategoryIdAndName
GoodsCategoryMapper.selectByPrimaryKey
NewBeeMallGoods.getGoodsName
NewBeeMallGoods.getGoodsIntro
NewBeeMallGoods.getGoodsId
NewBeeMallGoods.getGoodsCategoryId
NewBeeMallGoods.getTag
NewBeeMallGoods.setGoodsName
NewBeeMallGoods.setGoodsIntro
NewBeeMallGoods.setTag
NewBeeMallGoods.setUpdateTime
NewBeeMallUtils.cleanString
GoodsCategory.getCategoryLevel
```

**KuzuDB**: Precision 100% Recall 100% ✅ 完全匹配

---

### 4. NewBeeMallShoppingCartService.saveNewBeeMallCartItem

**Impl**: `NewBeeMallShoppingCartServiceImpl.saveNewBeeMallCartItem`
**上游** (1 调用者):
- `[controller] ShoppingCartController.saveNewBeeMallShoppingCartItem`

**下游** (9 被调用):
```
NewBeeMallShoppingCartItemMapper.selectByUserIdAndGoodsId
NewBeeMallShoppingCartItemMapper.selectCountByUserId
NewBeeMallShoppingCartItemMapper.insertSelective
NewBeeMallGoodsMapper.selectByPrimaryKey
NewBeeMallShoppingCartItem.getUserId
NewBeeMallShoppingCartItem.getGoodsId
NewBeeMallShoppingCartItem.getGoodsCount
NewBeeMallShoppingCartItem.setGoodsCount
NewBeeMallShoppingCartServiceImpl.updateNewBeeMallCartItem  (内调，含 5 个 2 级调用)
```

**KuzuDB**: Precision 100% Recall 100% ✅ 完全匹配

---

### 5. NewBeeMallOrderService.paySuccess

**Impl**: `NewBeeMallOrderServiceImpl.paySuccess`
**上游** (1 调用者):
- `[controller] OrderController.paySuccess`

**下游** (8 被调用):
```
NewBeeMallOrderMapper.selectByOrderNo
NewBeeMallOrderMapper.updateByPrimaryKeySelective
NewBeeMallOrder.getOrderStatus
NewBeeMallOrder.setOrderStatus
NewBeeMallOrder.setPayStatus
NewBeeMallOrder.setPayType
NewBeeMallOrder.setPayTime
NewBeeMallOrder.setUpdateTime
```

**KuzuDB**: Precision 100% Recall 80%
- FN(2): `getPayStatus`, `intValue` — AST 有但 KuzuDB 漏了（可能是 getter-style 过滤）

---

### 6. NewBeeMallCategoryService.getCategoriesForSearch

**Impl**: `NewBeeMallCategoryServiceImpl.getCategoriesForSearch`
**上游** (1 调用者):
- `[controller] GoodsController.searchPage`

**下游** (9 被调用):
```
GoodsCategoryMapper.selectByPrimaryKey
GoodsCategoryMapper.selectByLevelAndParentIdsAndNumber
GoodsCategory.getCategoryId
GoodsCategory.getCategoryLevel
GoodsCategory.getCategoryName
GoodsCategory.getParentId
SearchPageCategoryVO.setCurrentCategoryName
SearchPageCategoryVO.setSecondLevelCategoryName
SearchPageCategoryVO.setThirdLevelCategoryList
```

**KuzuDB**: Precision 100% Recall 81.8%
- FN(2): `getLevel`, `singletonList` — 枚举/集合方法，可能被 JDK 过滤

---

### 7. NewBeeMallGoodsService.searchNewBeeMallGoods

**Impl**: `NewBeeMallGoodsServiceImpl.searchNewBeeMallGoods`
**上游** (1 调用者):
- `[controller] GoodsController.searchPage`

**下游** (9 被调用):
```
NewBeeMallGoodsMapper.findNewBeeMallGoodsListBySearch
NewBeeMallGoodsMapper.getTotalNewBeeMallGoodsBySearch
BeanUtil.copyList
PageQueryUtil.getPage
PageQueryUtil.getLimit
NewBeeMallSearchGoodsVO.getGoodsName
NewBeeMallSearchGoodsVO.getGoodsIntro
NewBeeMallSearchGoodsVO.setGoodsName
NewBeeMallSearchGoodsVO.setGoodsIntro
```

**KuzuDB**: Precision 100% Recall 100% ✅ 完全匹配

---

### 8. NewBeeMallUserService.updateUserInfo

**Impl**: `NewBeeMallUserServiceImpl.updateUserInfo`
**上游** (1 调用者):
- `[controller] PersonalController.updateInfo`

**下游** (11 被调用):
```
MallUserMapper.selectByPrimaryKey
MallUserMapper.updateByPrimaryKeySelective
MallUser.getNickName
MallUser.getAddress
MallUser.getIntroduceSign
MallUser.setNickName
MallUser.setAddress
MallUser.setIntroduceSign
NewBeeMallUserVO.getUserId
NewBeeMallUtils.cleanString
BeanUtil.copyProperties
```

**KuzuDB**: Precision 100% Recall 100% ✅ 完全匹配

---

## 汇总指标

| 指标 | 值 |
|------|-----|
| True Positives | 91 |
| False Positives | 2 |
| False Negatives | 7 |
| **Precision** | **97.8%** |
| **Recall** | **92.9%** |
| **F1 Score** | **95.3%** |

## 完整调用链图（典型）

```
# 示例: OrderController.saveOrder 完整链路

OrderController.saveOrder
  ↓ CALLS
NewBeeMallOrderService.saveOrder (Interface)
  ↓ IMPLEMENTS
NewBeeMallOrderServiceImpl.saveOrder
  ├─→ NewBeeMallShoppingCartService.getMyShoppingCartItems
  │     ├─→ NewBeeMallShoppingCartItemMapper.selectByUserId
  │     ├─→ NewBeeMallGoodsMapper.selectByPrimaryKeys
  │     └─→ BeanUtil.copyProperties
  ├─→ NewBeeMallGoodsMapper.selectByPrimaryKeys
  ├─→ NewBeeMallGoodsMapper.updateStockNum
  ├─→ NewBeeMallShoppingCartItemMapper.deleteBatch
  ├─→ NewBeeMallOrderItemMapper.insertBatch
  ├─→ NewBeeMallOrderMapper.insertSelective
  ├─→ NumberUtil.genOrderNo
  │     └─→ NumberUtil.genRandomNum
  ├─→ BeanUtil.copyList
  ├─→ BeanUtil.copyProperties
  └─→ NewBeeMallException.fail
```

## 使用说明

每次修改调用链提取逻辑后，运行以下验证：

```bash
cd /home/claude/.cc-connect/workspace/pygitnexus
uv run python tests/verify_callchain.py /tmp/test-projects/newbee-mall
```

对比本文件中的 **汇总指标**，确保：
- Precision >= 95%
- Recall >= 90%
- F1 >= 92%

如果指标显著下降，检查上述 8 个方法中哪个出了问题。

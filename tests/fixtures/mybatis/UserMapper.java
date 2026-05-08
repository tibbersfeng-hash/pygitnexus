package com.example.demo.mapper;

import com.example.demo.entity.User;
import java.util.List;

public interface UserMapper {
    User selectById(Long id);
    User selectByUsername(String username);
    List<User> selectAll();
    int insert(User user);
    int updateStatus(User user);
    int deleteById(Long id);
}

package com.example.demo.service;

import com.example.demo.mapper.UserMapper;
import com.example.demo.entity.User;
import java.util.List;

public class UserService {

    private UserMapper userMapper;

    public User findById(Long id) {
        return userMapper.selectById(id);
    }

    public User findByUsername(String username) {
        return userMapper.selectByUsername(username);
    }

    public List<User> findAll() {
        return userMapper.selectAll();
    }

    public void createUser(User user) {
        userMapper.insert(user);
    }

    public void updateStatus(User user) {
        userMapper.updateStatus(user);
    }

    public void deleteUser(Long id) {
        userMapper.deleteById(id);
    }
}

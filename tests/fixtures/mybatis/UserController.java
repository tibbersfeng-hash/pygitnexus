package com.example.demo.controller;

import com.example.demo.service.UserService;
import com.example.demo.entity.User;
import java.util.List;

public class UserController {

    private UserService userService;

    @GetMapping("/users/{id}")
    public User getUser(Long id) {
        return userService.findById(id);
    }

    @GetMapping("/users")
    public List<User> getAllUsers() {
        return userService.findAll();
    }

    @PostMapping("/users")
    public void createUser(User user) {
        userService.createUser(user);
    }

    @PostMapping("/users/{id}/status")
    public void updateUserStatus(User user) {
        userService.updateStatus(user);
    }

    @PostMapping("/users/{id}/delete")
    public void deleteUser(Long id) {
        userService.deleteUser(id);
    }
}
